import asyncio
import json
from html.parser import HTMLParser
from importlib.resources import files

import pytest

from alpha_platform.cli import execute, main
from alpha_platform.domain import ResearchConfig
from alpha_platform.observability.queries import ObservatoryReader
from alpha_platform.observability.replay import build_replay_bundle
from alpha_platform.observability.replay_html import render_replay
from alpha_platform.persistence import Repository


class ScriptParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.active = False

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.scripts.append("")
            self.active = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.scripts[-1] += data


def test_html_uses_same_bundle_and_escaped_json_without_dynamic_code(tmp_path, monkeypatch):
    path = tmp_path / "html.sqlite3"
    repo = Repository(path)
    repo.initialize()
    report = asyncio.run(execute(repo, ResearchConfig()))
    reader = ObservatoryReader(path)
    try:
        raw = reader.snapshot(report["id"], for_replay=True)
    finally:
        reader.close()
        repo.close()
    injection = (
        "</script><script>window.__replay_injected=1</script><img onerror=alert(1)>\u2028\u2029<&"
    )
    raw["events"][0]["payload"]["expression"] = injection
    monkeypatch.setenv("WQ_TOKEN", "MUST-STAY-PRIVATE")
    raw["events"][0]["payload"]["summary"] = "MUST-STAY-PRIVATE"
    bundle = build_replay_bundle(raw)
    document = render_replay(bundle)
    parsed = ScriptParser()
    parsed.feed(document)
    assert len(parsed.scripts) == 2
    assert json.loads(parsed.scripts[0]) == bundle
    assert "<script>window.__replay_injected" not in document
    assert "MUST-STAY-PRIVATE" not in document
    assert "\\u003c/script\\u003e" in document
    assert '<html lang="zh-CN">' in document
    for label in (
        "Alpha 研究执行回放",
        "回到开头",
        "上一步",
        "下一步",
        "播放速度",
        "上一个异常",
        "下一个异常",
        "证据检查",
        "最终查询快照",
    ):
        assert label in document
    player = (
        files("alpha_platform.observability")
        .joinpath("replay_player.js")
        .read_text(encoding="utf-8")
    )
    assert "innerHTML" not in player and "eval(" not in player
    assert not any(
        api in player for api in ("fetch(", "WebSocket(", "XMLHttpRequest", "document.cookie")
    )


def test_default_replay_cli_generates_html_without_changing_old_report(tmp_path, capsys):
    db = tmp_path / "cli.sqlite3"
    repo = Repository(db)
    repo.initialize()
    report = asyncio.run(execute(repo, ResearchConfig()))
    repo.close()
    destination = tmp_path / "player.html"
    assert (
        main(
            [
                "--db",
                str(db),
                "observe",
                "replay",
                "--run-id",
                report["id"],
                "--output",
                str(destination),
            ]
        )
        == 0
    )
    assert "Alpha 研究执行回放" in destination.read_text(encoding="utf-8")
    capsys.readouterr()
    assert (
        main(
            [
                "--db",
                str(db),
                "observe",
                "report",
                "--run-id",
                report["id"],
                "--output",
                str(tmp_path / "old.html"),
            ]
        )
        == 0
    )
    assert "Research Observatory" in (tmp_path / "old.html").read_text(encoding="utf-8")


def test_replay_rejects_silent_history_filtering():
    with pytest.raises(SystemExit) as exc:
        main(["observe", "replay", "--run-id", "r", "--output", "unused.html", "--errors"])
    assert exc.value.code == 2
