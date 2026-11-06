"""Unit tests (stdlib unittest). Run from the repo root: python -m unittest discover -s tests -v"""
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "komodo-deploy" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from komodo_deploy import cli, client, config, doctor, stacks, syncs  # noqa: E402
from komodo_deploy.client import KomodoError, update_id, wait_update  # noqa: E402

ENV = {config.KEY_ENV: "K-x", config.SECRET_ENV: "S-x"}


class NormalizeHostTest(unittest.TestCase):
    def test_adds_https_to_bare_hostname(self):
        self.assertEqual(config.normalize_host("komodo.example.com"), "https://komodo.example.com")

    def test_strips_trailing_slash_and_keeps_scheme(self):
        self.assertEqual(config.normalize_host("http://k.example:9120/"), "http://k.example:9120")

    def test_rejects_empty(self):
        with self.assertRaises(config.ConfigError):
            config.normalize_host("  ")


class ResolveSettingsTest(unittest.TestCase):
    def test_flag_overrides_env_and_file(self):
        s = config.resolve_settings("flag.example", {**ENV, "KOMODO_HOST": "env.example"},
                                    {"default_host": "file.example"})
        self.assertEqual(s.host, "https://flag.example")

    def test_env_overrides_file(self):
        s = config.resolve_settings(None, {**ENV, "KOMODO_HOST": "env.example"},
                                    {"default_host": "file.example"})
        self.assertEqual(s.host, "https://env.example")

    def test_file_default_host_used_last(self):
        s = config.resolve_settings(None, ENV, {"default_host": "file.example"})
        self.assertEqual(s.host, "https://file.example")

    def test_missing_host_raises_with_hint(self):
        with self.assertRaisesRegex(config.ConfigError, "KOMODO_HOST"):
            config.resolve_settings(None, ENV, {})

    def test_missing_credentials_raises(self):
        with self.assertRaisesRegex(config.ConfigError, "KOMODO_DEPLOY_API_KEY"):
            config.resolve_settings("k.example", {}, {})

    def test_host_profile_overrides_env_names_and_options(self):
        file_cfg = {"hosts": {"k2.example": {"key_env": "K2", "secret_env": "S2",
                                             "sync": "s2", "insecure": True}}}
        s = config.resolve_settings("k2.example", {"K2": "a", "S2": "b"}, file_cfg)
        self.assertEqual((s.key, s.secret, s.sync_name, s.insecure), ("a", "b", "s2", True))

    def test_sync_repo_has_no_default(self):
        self.assertIsNone(config.resolve_settings("k.example", ENV, {}).sync_repo)


class ClientTest(unittest.TestCase):
    def test_v2_path_style_url(self):
        self.assertEqual(client.request_url("https://k.example", "read", "ListStacks"),
                         "https://k.example/read/ListStacks")

    def test_list_all_disables_pagination(self):
        settings = config.resolve_settings("k.example", ENV, {})
        c = client.Client(settings)
        with mock.patch.object(c, "call", return_value=[]) as call:
            c.list_all("ListStacks", {"query": {}})
        call.assert_called_once_with("read", "ListStacks", {"query": {}, "limit": 0})


class GitRemoteTest(unittest.TestCase):
    def test_parses_common_remote_forms(self):
        cases = {
            "https://github.com/acme/web.git": "acme/web",
            "git@github.com:acme/web.git": "acme/web",
            "https://github.com/acme/web": "acme/web",
            "ssh://git@git.example.com:2222/acme/web.git": "acme/web",
            "https://gitlab.example.com/group/sub/web.git": "group/sub/web",
            "git@gitlab.example.com:group/sub/web.git": "group/sub/web",
        }
        for url, expected in cases.items():
            self.assertEqual(stacks.parse_git_remote(url), expected, url)

    def test_invalid_remote(self):
        self.assertIsNone(stacks.parse_git_remote(""))


STACKS = [
    {"name": "web", "id": "1", "info": {"repo": "acme/web"}},
    {"name": "edge-proxy", "id": "2", "info": {"repo": "acme/infra"}},
    {"name": "monitoring", "id": "3", "info": {"repo": "acme/infra"}},
]


class MatchStacksTest(unittest.TestCase):
    def test_exact_name_wins_over_substring(self):
        self.assertEqual([s["name"] for s in stacks.match_stacks(STACKS, "web", None)], ["web"])

    def test_substring_fallback(self):
        names = [s["name"] for s in stacks.match_stacks(STACKS, "proxy", None)]
        self.assertEqual(names, ["edge-proxy"])

    def test_match_by_repo_slug(self):
        names = [s["name"] for s in stacks.match_stacks(STACKS, None, "acme/infra")]
        self.assertEqual(names, ["edge-proxy", "monitoring"])

    def test_no_query_returns_empty(self):
        self.assertEqual(stacks.match_stacks(STACKS, None, None), [])


class MonoRepoTest(unittest.TestCase):
    CANDIDATES = [({"name": "proxy"}, "edge-proxy"), ({"name": "mon"}, "monitoring/core"),
                  ({"name": "mon-agent"}, "monitoring/core/agent"), ({"name": "root"}, "")]

    def pick(self, prefix):
        return [s["name"] for s in stacks.pick_by_run_directory(self.CANDIDATES, prefix)]

    def test_exact_run_directory(self):
        self.assertEqual(self.pick("edge-proxy/"), ["proxy"])

    def test_deepest_match_wins_from_subdirectory(self):
        self.assertEqual(self.pick("monitoring/core/agent/config/"), ["mon-agent"])

    def test_repo_root_selects_root_stack(self):
        self.assertEqual(self.pick(""), ["root"])

    def test_unrelated_directory_matches_nothing(self):
        self.assertEqual(self.pick("docs/"), [])


class ImageChangesTest(unittest.TestCase):
    def test_reports_only_changed_services(self):
        before = [{"service_name": "a", "image": "x:1"}, {"service_name": "b", "image": "y:1"}]
        after = [{"service_name": "a", "image": "x:2"}, {"service_name": "b", "image": "y:1"}]
        self.assertEqual(stacks.image_changes(before, after),
                         [{"service": "a", "from": "x:1", "to": "x:2"}])

    def test_compose_template_reported_as_unresolved(self):
        before = [{"service_name": "a", "image": "x:1"}]
        after = [{"service_name": "a", "image": "x:${TAG:-1}"}]
        change = stacks.image_changes(before, after)[0]
        self.assertEqual((change["to"], change["unresolved"]), (None, "x:${TAG:-1}"))


def svc(name, state, status="", top_state=None):
    item = {"service": name, "container": {"state": state, "status": status}}
    if top_state:
        item["state"] = top_state
    return item


class HealthTest(unittest.TestCase):
    def test_all_running_is_healthy(self):
        result = stacks.evaluate_health([svc("a", "running", "Up 2m (healthy)")])
        self.assertTrue(result["healthy"])

    def test_restart_loop_detected(self):
        result = stacks.evaluate_health([svc("a", "restarting", "Restarting (1) 3s ago")])
        self.assertEqual(result["problems"][0]["issue"], "restart loop")

    def test_unhealthy_and_exited_are_problems(self):
        result = stacks.evaluate_health([svc("a", "running", "Up (unhealthy)"),
                                         svc("b", "exited", "Exited (1)")])
        self.assertEqual([p["issue"] for p in result["problems"]], ["unhealthy", "exited"])

    def test_health_starting_is_pending(self):
        result = stacks.evaluate_health([svc("a", "running", "Up 5s (health: starting)")])
        self.assertEqual((result["healthy"], result["pending"]), (False, ["a"]))

    def test_init_container_exited_zero_is_ok(self):
        result = stacks.evaluate_health([svc("a", "running", "Up"),
                                         svc("init", "exited", "Exited (0) 4 days ago")])
        self.assertTrue(result["healthy"])

    def test_ignored_service_skipped(self):
        result = stacks.evaluate_health([svc("x", "exited", "Exited (1)")], ignore=["x"])
        self.assertTrue(result["healthy"])

    def test_missing_container_is_problem(self):
        result = stacks.evaluate_health([{"service": "a", "container": None}])
        self.assertEqual(result["problems"][0]["issue"], "missing")

    def test_swarm_service_uses_top_level_state(self):
        result = stacks.evaluate_health([{"service": "a", "container": None,
                                          "swarm_service": {"name": "a"}, "state": "Running"}])
        self.assertTrue(result["healthy"])

    def test_top_level_state_overrides_container(self):
        result = stacks.evaluate_health([svc("a", "running", "Up", top_state="Dead")])
        self.assertEqual(result["problems"][0]["issue"], "dead")


class DiagnoseTest(unittest.TestCase):
    def test_classifies_known_failures(self):
        cases = {
            "Error: pull access denied for foo": "image_pull",
            "Bind for 0.0.0.0:80 failed: port is already allocated": "port_conflict",
            "network proxy declared as external, but could not be found": "network_missing",
            "PASSWORD=[[WEB_DB_PASS]]": "missing_variable",
        }
        for text, kind in cases.items():
            self.assertIn(kind, [f["kind"] for f in stacks.diagnose(text)], text)

    def test_unknown_text_yields_nothing(self):
        self.assertEqual(stacks.diagnose("all good"), [])


class DisplayUserTest(unittest.TestCase):
    def test_system_ids_labelled(self):
        self.assertEqual(stacks.display_user("000000000000000000000005"), "system (5)")

    def test_normal_username_unchanged(self):
        self.assertEqual(stacks.display_user("admin"), "admin")


class SummarizeUpdateTest(unittest.TestCase):
    def test_finds_failed_stage(self):
        update = {"success": False, "operation": "DeployStack", "logs": [
            {"stage": "Clone", "success": True},
            {"stage": "Compose Up", "success": False, "stderr": "boom", "command": "up"}]}
        result = stacks.summarize_update(update)
        self.assertEqual(result["failed_stage"]["stage"], "Compose Up")
        self.assertFalse(result["success"])


def sync_with(contents):
    return {"name": "main-sync", "config": {"repo": "acme/komodo", "resource_path": ["res"]},
            "info": {"remote_contents": [{"path": "stacks.toml", "contents": contents}]}}


class SyncTest(unittest.TestCase):
    def test_flags_updates_touching_stack(self):
        info = {"resource_updates": [
            {"target": {"type": "Stack", "id": "1"}, "data": {"name": "web"}},
            {"target": {"type": "Stack", "id": "9"}, "data": {"name": "other"}}]}
        result = syncs.pending_for(info, "web", "1")
        self.assertTrue(result["affects_stack"])
        self.assertEqual(len(result["other_updates"]), 1)

    def test_no_updates(self):
        self.assertFalse(syncs.pending_for({}, "web", "1")["affects_stack"])

    def test_defines_stack_exact_name_only(self):
        sync = sync_with('[[stack]]\nname = "web"\n[stack.config]\n\n[[stack]]\nname = "web2"\n')
        self.assertTrue(syncs.defines_stack(sync, "web"))
        self.assertTrue(syncs.defines_stack(sync, "web2"))
        self.assertFalse(syncs.defines_stack(sync, "we"))

    def test_deployment_with_same_name_is_not_a_stack(self):
        self.assertFalse(syncs.defines_stack(sync_with('[[deployment]]\nname = "web"\n'), "web"))

    def test_describe_lists_layout(self):
        result = syncs.describe(sync_with(""))
        self.assertEqual((result["repo"], result["resource_path"], result["files"]),
                         ("acme/komodo", ["res"], ["stacks.toml"]))


class DoctorTest(unittest.TestCase):
    def test_version_gate(self):
        self.assertTrue(doctor.version_ok("2.3.3"))
        self.assertFalse(doctor.version_ok("1.19.5"))
        self.assertFalse(doctor.version_ok(None))

    def test_setup_commands_exist_for_every_platform(self):
        for name in ("windows", "macos", "linux", "wsl"):
            with mock.patch.object(config, "platform_name", return_value=name):
                text = doctor.setup_commands()
            self.assertIn("KOMODO_DEPLOY_API_SECRET", text, name)
            self.assertIn(doctor.EXAMPLE_HOST, text, name)


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def read(self, rtype, params=None):
        self.calls.append(rtype)
        value = self.responses[rtype]
        return value.pop(0) if isinstance(value, list) and value and rtype == "GetUpdate" else value

    def list_all(self, rtype, params=None):
        return self.read(rtype, params)


class WaitUpdateTest(unittest.TestCase):
    def test_polls_until_complete(self):
        fake = FakeClient({"GetUpdate": [{"status": "InProgress", "logs": []},
                                         {"status": "Complete", "success": True, "logs": []}]})
        with redirect_stdout(io.StringIO()):
            result = wait_update(fake, "u1", timeout_s=10, interval_s=0, sleep=lambda _: None)
        self.assertTrue(result["success"])
        self.assertEqual(fake.calls.count("GetUpdate"), 2)

    def test_times_out(self):
        fake = FakeClient({"GetUpdate": {"status": "InProgress", "logs": []}})
        with self.assertRaises(KomodoError):
            wait_update(fake, "u1", timeout_s=0, interval_s=0, sleep=lambda _: None)

    def test_update_id_variants(self):
        self.assertEqual(update_id({"id": "a"}), "a")
        self.assertEqual(update_id({"_id": {"$oid": "b"}}), "b")


class CliTest(unittest.TestCase):
    def run_cli(self, fake, argv):
        out = io.StringIO()
        with mock.patch.object(cli, "_client", return_value=fake), redirect_stdout(out):
            code = cli.main(argv)
        return code, out.getvalue()

    def test_vars_check_never_outputs_values(self):
        fake = FakeClient({"ListVariables": [
            {"name": "A", "value": "TOPSECRET", "is_secret": True},
            {"name": "B", "value": "plain", "is_secret": False}]})
        code, text = self.run_cli(fake, ["vars-check", "--names", "A,B,C"])
        payload = json.loads(text)
        self.assertNotIn("TOPSECRET", text)
        self.assertEqual((payload["missing"], payload["not_secret"]), (["C"], ["B"]))
        self.assertEqual(code, cli.EXIT_FAILED)

    def test_config_error_exit_code(self):
        out = io.StringIO()
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch.object(config, "load_file_config", return_value={}), \
                redirect_stdout(out):
            code = cli.main(["whoami"])
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("setup", json.loads(out.getvalue()))


if __name__ == "__main__":
    unittest.main()
