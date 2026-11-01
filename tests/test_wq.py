import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from alpha_platform.domain import ResearchConfig, SimulationRequest
from alpha_platform.persistence import Repository
from alpha_platform.validation import fingerprints
from alpha_platform.wq.adapter import WQSimulationAdapter, retry_seconds
from alpha_platform.wq.config import load_live_config
from alpha_platform.wq.multi import WQMultiAdapter
from alpha_platform.wq.runner import credentials, prepare, run_batch
from alpha_platform.wq.transport import BASE, Response, WQError, checked_url


def config():
    return load_live_config(Path(__file__).parents[1] / "configs/wq-live-example.json")


async def no_sleep(seconds):
    assert seconds >= 0


class FakeWQ:
    """Contract fixtures, not claimed to be captured production responses."""

    def __init__(self, scenario="success"):
        self.scenario = scenario
        self.posts = []
        self.simulations = {}
        self.alphas = {}
        self.auth = 0
        self.gets = []

    def request(self, method, url, payload=None, credentials=None):
        if url == BASE + "/authentication":
            self.auth += 1
            assert credentials == ("fixture-user", "fixture-secret")
            return Response(201, {})
        assert credentials is None
        if method == "POST":
            self.posts.append(payload)
            if self.scenario == "timeout":
                raise TimeoutError()
            if self.scenario == "rate" and len(self.posts) == 1:
                return Response(429, {"retry-after": "0"})
            if self.scenario == "missing_location":
                return Response(201, {})
            parent = "parent" + str(len(self.posts))
            items = payload if isinstance(payload, list) else [payload]
            children = []
            for i, item in enumerate(items):
                child, alpha = f"{parent}child{i}", f"{parent}alpha{i}"
                children.append(child)
                self.simulations[child] = {"status": "COMPLETE", "alpha": alpha}
                self.alphas[alpha] = {
                    "regular": {"code": item["regular"]},
                    "settings": item["settings"],
                    "is": {"sharpe": float(i + 1), "checks": [{"name": "TEST", "result": "PASS"}]},
                    "private": "DO_NOT_PERSIST",
                }
            self.simulations[parent] = (
                {"status": "COMPLETE", "children": children[::-1]}
                if isinstance(payload, list)
                else self.simulations[children[0]]
            )
            if self.scenario == "ambiguous":
                for alpha in self.alphas.values():
                    alpha["regular"]["code"] = "close"
            if self.scenario in {"omit_period", "different_period", "omit_decay"}:
                for alpha in self.alphas.values():
                    alpha["settings"] = dict(alpha["settings"])
                    if self.scenario == "omit_period":
                        alpha["settings"].pop("testPeriod")
                    elif self.scenario == "different_period":
                        alpha["settings"]["testPeriod"] = "P1Y"
                    else:
                        alpha["settings"].pop("decay")
            if self.scenario == "failed":
                self.simulations[parent] = {"status": "ERROR"}
            return Response(201, {"location": "/simulations/" + parent})
        self.gets.append(url)
        if "/simulations/" in url:
            return Response(200, {}, self.simulations[url.rsplit("/", 1)[1]])
        return Response(200, {}, self.alphas[url.rsplit("/", 1)[1]])


def execute(tmp_path, expressions, batch_size=8, concurrent=1, scenario="success"):
    transport = FakeWQ(scenario)
    repo = Repository(tmp_path / "live.sqlite3")
    repo.initialize()

    async def run():
        adapter = WQMultiAdapter(transport, batch_size, sleep=no_sleep)
        await adapter.authenticate("fixture-user", "fixture-secret")
        return await run_batch(
            repo, adapter, prepare(config(), expressions, len(expressions)), concurrent
        )

    try:
        reports = asyncio.run(run())
        return reports, transport
    finally:
        repo.close()


def test_multi_reverse_order_mapping_and_evidence(tmp_path):
    reports, transport = execute(tmp_path, ["close", "rank(close)"])
    assert len(transport.posts) == 1
    assert isinstance(transport.posts[0], list)
    by_expression = {r["jobs"][0]["expression"]: r for r in reports}
    for i, expression in enumerate(["close", "rank(close)"]):
        r = by_expression[expression]
        assert r["status"] == "COMPLETED"
        assert r["used_budget"] == 1 and r["reserved_budget"] == 0
        assert json.loads(r["jobs"][0]["metrics_json"])["sharpe"] == i + 1
        assert r["phase"] == "M1.5" and r["summary"]["data_version"] == "wq-live-v1"
        assert "MOCK_GATE" not in json.dumps(r)
        assert "DO_NOT_PERSIST" not in json.dumps(r)
        receipt = json.loads(r["jobs"][0]["remote_id"])
        assert receipt["expression"] == expression
        assert receipt["parent"].endswith("parent1")
    # One shared parent lookup + one child lookup and one Alpha lookup per child.
    assert len(transport.gets) == 5


@pytest.mark.parametrize("batch_size,concurrent,count", [(8, 6, 48), (8, 10, 80), (8, 6, 49)])
def test_requested_batch_shapes_and_tail(tmp_path, batch_size, concurrent, count):
    expressions = ["ts_mean(close, %d)" % (i + 1) for i in range(count)]
    cfg = replace(config(), operators={"ts_mean": 2})
    transport = FakeWQ()
    repo = Repository(tmp_path / "scale.sqlite3")
    repo.initialize()

    async def run():
        adapter = WQMultiAdapter(transport, batch_size, sleep=no_sleep)
        await adapter.authenticate("fixture-user", "fixture-secret")
        return await run_batch(repo, adapter, prepare(cfg, expressions, count), concurrent)

    try:
        reports = asyncio.run(run())
        assert all(r["status"] == "COMPLETED" for r in reports)
        assert len(reports) == count
        assert len(transport.posts) == (count + batch_size - 1) // batch_size
        assert all(not isinstance(p, list) or len(p) == batch_size for p in transport.posts)
    finally:
        repo.close()


@pytest.mark.parametrize("scenario", ["timeout", "missing_location"])
def test_ambiguous_batch_keeps_every_reservation_and_blocks_duplicates(tmp_path, scenario):
    reports, transport = execute(tmp_path, ["close", "rank(close)"], scenario=scenario)
    assert len(transport.posts) == 1
    assert all(
        r["status"] == "FAILED" and r["reserved_budget"] == 1 and r["used_budget"] == 0
        for r in reports
    )
    assert all(r["jobs"][0]["status"] == "UNKNOWN" for r in reports)
    again, _ = execute(tmp_path, ["close", "rank(close)"])
    assert all(r["status"] == "REJECTED" for r in again)


def test_explicit_rate_limit_retries_without_double_charge(tmp_path):
    reports, transport = execute(tmp_path, ["close", "rank(close)"], scenario="rate")
    assert len(transport.posts) == 2
    assert all(r["used_budget"] == 1 and r["reserved_budget"] == 0 for r in reports)
    assert all(len(r["attempts"]) == 2 for r in reports)


def test_parent_remote_failure_is_not_a_success(tmp_path):
    reports, _ = execute(tmp_path, ["close", "rank(close)"], scenario="failed")
    assert all(r["jobs"][0]["status"] == "FAILED" and r["used_budget"] == 1 for r in reports)


def test_ambiguous_children_never_assigned_by_position(tmp_path):
    reports, _ = execute(tmp_path, ["close", "rank(close)"], scenario="ambiguous")
    assert all(r["jobs"][0]["status"] == "UNKNOWN" for r in reports)
    assert all(r["used_budget"] == 1 and r["reserved_budget"] == 0 for r in reports)


def test_singleton_uses_object_payload(tmp_path):
    reports, transport = execute(tmp_path, ["rank(close)"])
    assert isinstance(transport.posts[0], dict)
    assert reports[0]["status"] == "COMPLETED"


def test_unknown_halts_new_groups_without_resubmitting(tmp_path):
    reports, transport = execute(
        tmp_path, ["close", "rank(close)"], batch_size=1, scenario="timeout"
    )
    assert len(transport.posts) == 1 and len(reports) == 1
    assert reports[0]["jobs"][0]["status"] == "UNKNOWN"


@pytest.mark.parametrize("expressions", [["close"], ["close", "rank(close)"]])
def test_live_verified_missing_test_period_is_an_explicit_gap(tmp_path, expressions):
    reports, _ = execute(tmp_path, expressions, scenario="omit_period")
    assert all(r["status"] == "COMPLETED" for r in reports)
    for r in reports:
        raw = json.loads(r["jobs"][0]["raw_json"])
        assert raw["evidence_gaps"] == ["WQ_TEST_PERIOD_NOT_ECHOED"]
    from alpha_platform.observability.queries import ObservatoryReader

    reader = ObservatoryReader(tmp_path / "live.sqlite3")
    try:
        snapshot = reader.snapshot(reports[0]["id"], for_replay=True)
        assert any(g.startswith("WQ_TEST_PERIOD_NOT_ECHOED:") for g in snapshot["evidence_gaps"])
    finally:
        reader.close()


@pytest.mark.parametrize("scenario", ["different_period", "omit_decay"])
@pytest.mark.parametrize("expressions", [["close"], ["close", "rank(close)"]])
def test_actual_setting_mismatches_still_reject_results(tmp_path, expressions, scenario):
    reports, _ = execute(tmp_path, expressions, scenario=scenario)
    assert all(r["jobs"][0]["status"] == "UNKNOWN" for r in reports)


def test_identity_includes_backend_and_all_settings():
    cfg = config()
    changed = replace(cfg, simulation_settings={**cfg.settings(), "decay": 5})
    mock = ResearchConfig(
        region=cfg.region, universe=cfg.universe, fields=cfg.fields, operators=cfg.operators
    )
    assert fingerprints("rank(close)", cfg)[0] != fingerprints("rank(close)", changed)[0]
    assert fingerprints("rank(close)", cfg)[0] != fingerprints("rank(close)", mock)[0]


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/simulations/abc",
        "//evil/simulations/a",
        "/simulations/../authentication",
        "/simulations/a?token=secret",
        "/simulations/a#b",
        "http://api.worldquantbrain.com/simulations/a",
    ],
)
def test_untrusted_locations_rejected(url):
    with pytest.raises(WQError):
        checked_url(url, "simulations")


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "garbage", "99999"])
def test_bad_retry_after_rejected(value):
    with pytest.raises(WQError):
        retry_seconds({"retry-after": value})


def test_credentials_local_only(tmp_path, monkeypatch):
    monkeypatch.delenv("WQ_USERNAME", raising=False)
    monkeypatch.delenv("WQ_PASSWORD", raising=False)
    file = tmp_path / ".env.wq"
    file.write_text("WQ_USERNAME=fixture-user\nWQ_PASSWORD=fixture-secret\n", encoding="utf-8")
    assert credentials(file) == ("fixture-user", "fixture-secret")


@pytest.mark.parametrize("quote", ["'", '"'])
def test_credential_outer_quotes_are_not_sent_as_password(tmp_path, monkeypatch, quote):
    monkeypatch.delenv("WQ_USERNAME", raising=False)
    monkeypatch.delenv("WQ_PASSWORD", raising=False)
    file = tmp_path / ".env.wq"
    file.write_text(
        f"WQ_USERNAME={quote}fixture-user{quote}\nWQ_PASSWORD={quote} p=a#$x\\z {quote}\n",
        encoding="utf-8",
    )
    assert credentials(file) == ("fixture-user", " p=a#$x\\z ")


def test_environment_credentials_remain_literal(tmp_path, monkeypatch):
    monkeypatch.setenv("WQ_USERNAME", "fixture-user")
    monkeypatch.setenv("WQ_PASSWORD", '"literal-quotes"')
    assert credentials(tmp_path / "missing") == ("fixture-user", '"literal-quotes"')


def test_preflight_invalid_input_no_network():
    with pytest.raises(ValueError):
        prepare(config(), ["close", "rank(close)"], 1)
    with pytest.raises(ValueError):
        prepare(config(), ["close + close"], 1)


def test_pending_and_malformed_contracts():
    class Transport:
        def __init__(self, body):
            self.body = body

        def request(self, *args):
            return Response(200, {"retry-after": "0"}, self.body)

    async def run():
        adapter = WQSimulationAdapter(Transport({"status": "RUNNING"}), sleep=no_sleep)
        assert await adapter.poll(BASE + "/simulations/abc") == "RUNNING"
        for body in [[], None, {"status": "MYSTERY"}, {"status": "COMPLETE"}]:
            adapter = WQSimulationAdapter(Transport(body), sleep=no_sleep)
            with pytest.raises(WQError):
                await adapter.poll(BASE + "/simulations/abc")

    asyncio.run(run())


def test_read_only_transport_retry_does_not_repeat_post():
    class Transport:
        def __init__(self):
            self.gets = 0

        def request(self, method, *args):
            assert method == "GET"
            self.gets += 1
            if self.gets < 3:
                raise WQError("WQ_TRANSPORT_UNAVAILABLE")
            return Response(200, {}, {"status": "COMPLETE", "alpha": "abc"})

    async def run():
        transport = Transport()
        adapter = WQSimulationAdapter(transport, sleep=no_sleep)
        assert await adapter.poll(BASE + "/simulations/abc") == "COMPLETED"
        assert transport.gets == 3

    asyncio.run(run())


def test_rolling_batches_do_not_wait_for_slowest_group(tmp_path):
    class Rolling(FakeWQ):
        def __init__(self):
            super().__init__()
            self.first_completed_after_posts = None

        def request(self, method, url, payload=None, credentials=None):
            if method == "GET" and url.endswith("/parent1"):
                if len(self.posts) < 3:
                    return Response(200, {"retry-after": "0"}, {"status": "RUNNING"})
                self.first_completed_after_posts = len(self.posts)
            return super().request(method, url, payload, credentials)

    transport = Rolling()
    cfg = replace(config(), operators={"ts_mean": 2})
    repo = Repository(tmp_path / "rolling.sqlite3")
    repo.initialize()

    async def run():
        adapter = WQMultiAdapter(transport, 2, sleep=no_sleep)
        await adapter.authenticate("fixture-user", "fixture-secret")
        expressions = [f"ts_mean(close, {i})" for i in range(1, 7)]
        return await run_batch(repo, adapter, prepare(cfg, expressions, 6), 2)

    try:
        reports = asyncio.run(run())
        assert all(r["status"] == "COMPLETED" for r in reports)
        assert transport.first_completed_after_posts == 3
    finally:
        repo.close()


def test_accepted_receipt_resolves_with_fresh_adapter():
    async def run():
        transport = FakeWQ()
        adapter = WQMultiAdapter(transport, 8, sleep=no_sleep)
        await adapter.authenticate("fixture-user", "fixture-secret")
        receipts = await asyncio.gather(
            *(
                adapter.submit(SimulationRequest(e, config().settings()))
                for e in ["close", "rank(close)"]
            )
        )
        fresh = WQMultiAdapter(transport, 8, sleep=no_sleep)
        for i, receipt in enumerate(receipts):
            assert await fresh.poll(receipt.remote_id) == "COMPLETED"
            assert (await fresh.fetch_result(receipt.remote_id)).metrics["sharpe"] == i + 1
        assert len(transport.posts) == 1

    asyncio.run(run())


def test_read_model_and_replay_use_existing_evidence(tmp_path):
    from alpha_platform.observability.queries import ObservatoryReader
    from alpha_platform.observability.replay import build_replay_bundle

    reports, _ = execute(tmp_path, ["rank(close)"])
    reader = ObservatoryReader(tmp_path / "live.sqlite3")
    try:
        snapshot = reader.snapshot(reports[0]["id"], for_replay=True)
        bundle = build_replay_bundle(snapshot)
        assert bundle
        assert "LOCAL_RESEARCH_GATE_PASS" in json.dumps(snapshot)
        assert "fixture-secret" not in json.dumps(bundle)
    finally:
        reader.close()


def test_inspect_does_not_resubmit_or_modify_frozen_task(tmp_path, monkeypatch, capsys):
    from alpha_platform.cli import main
    from alpha_platform.wq import cli

    reports, transport = execute(tmp_path, ["rank(close)"], scenario="different_period")
    run_id = reports[0]["id"]
    assert reports[0]["jobs"][0]["status"] == "UNKNOWN"
    for alpha in transport.alphas.values():
        alpha["settings"]["testPeriod"] = "P0Y"
    monkeypatch.setattr(cli, "HTTPTransport", lambda: transport)
    monkeypatch.setattr(cli, "credentials", lambda _: ("fixture-user", "fixture-secret"))
    assert main(["--db", str(tmp_path / "live.sqlite3"), "wq", "inspect", "--run-id", run_id]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["remote_status"] == "COMPLETED"
    assert output["local_job_status"] == "UNKNOWN"
    assert output["local_run_status"] == "FAILED"
    assert output["simulation_submitted"] is False and output["database_modified"] is False
    assert len(transport.posts) == 1
    repo = Repository(tmp_path / "live.sqlite3")
    try:
        assert repo.report(run_id) == reports[0]
    finally:
        repo.close()
