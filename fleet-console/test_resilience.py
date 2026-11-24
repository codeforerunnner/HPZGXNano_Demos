import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import launcher_web


class ResilienceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.orig_demos_root = launcher_web.DEMOS_ROOT
        self.orig_config = launcher_web.CONFIG_FILE
        self.orig_fleet = launcher_web.FLEET_FILE
        launcher_web.DEMOS_ROOT = self.root
        launcher_web.CONFIG_FILE = self.root / "console-config.json"
        launcher_web.FLEET_FILE = self.root / "fleet.json"
        with launcher_web.RESILIENCE_LOCK:
            launcher_web._RESILIENCE_STATE["master_failures"] = 0
            launcher_web._RESILIENCE_STATE["last_announced_ip"] = ""

    def tearDown(self):
        launcher_web.DEMOS_ROOT = self.orig_demos_root
        launcher_web.CONFIG_FILE = self.orig_config
        launcher_web.FLEET_FILE = self.orig_fleet
        self._tmp.cleanup()

    def test_identity_and_hub_epoch_persisted(self):
        cfg = launcher_web.load_config()
        self.assertTrue(cfg["node_id"])
        self.assertEqual(cfg["role"], "hub")
        self.assertEqual(cfg["hub_epoch"], 1)

        launcher_web.set_local_role("node")
        cfg_node = launcher_web.load_config()
        self.assertEqual(cfg_node["role"], "node")
        self.assertEqual(cfg_node["hub_epoch"], 1)

        launcher_web.set_local_role("hub", min_epoch=4)
        cfg_hub = launcher_web.load_config()
        self.assertEqual(cfg_hub["role"], "hub")
        self.assertEqual(cfg_hub["hub_epoch"], 5)

    def test_reconcile_fleet_ips_updates_moved_hub_and_node(self):
        launcher_web.CONFIG_FILE.write_text(
            json.dumps({"role": "node", "node_token": "local-tok", "node_id": "n-local", "hub_epoch": 0}),
            encoding="utf-8",
        )
        launcher_web.save_fleet([
            {"id": "hub-1", "alias": "192.168.1.74", "host": "192.168.1.74", "port": 18888, "token": "hub-tok", "role": "hub"},
            {"id": "node-2", "alias": "192.168.1.85", "host": "192.168.1.85", "port": 18888, "token": "node2-tok", "role": "node"},
        ])

        candidates = [
            {"host": "192.168.1.113", "port": 18888, "token_sha256": launcher_web._token_digest("hub-tok"), "role": "hub", "hub_epoch": 2},
            {"host": "192.168.1.137", "port": 18888, "token_sha256": launcher_web._token_digest("node2-tok"), "role": "node", "hub_epoch": 0},
        ]

        def fake_http(host, port, path, token, **kwargs):
            if host == "192.168.1.113" and token == "hub-tok":
                return {"ok": True, "role": "hub", "hub_epoch": 2}
            if host == "192.168.1.137" and token == "node2-tok":
                return {"ok": True, "role": "node", "hub_epoch": 0}
            raise OSError("unreachable")

        with mock.patch.object(launcher_web, "discover_zgx_candidates", return_value=candidates), \
             mock.patch.object(launcher_web, "_node_http_json", side_effect=fake_http):
            res = launcher_web.reconcile_fleet_ips()

        self.assertEqual(len(res["updated"]), 2)
        self.assertEqual(res["discovered_hub"]["host"], "192.168.1.113")
        fleet = launcher_web.load_fleet()
        self.assertEqual(fleet[0]["host"], "192.168.1.113")
        self.assertEqual(fleet[0]["alias"], "192.168.1.113")
        self.assertEqual(fleet[1]["host"], "192.168.1.137")

    def test_handle_fleet_announce_updates_known_node_only(self):
        launcher_web.CONFIG_FILE.write_text(
            json.dumps({"role": "hub", "node_token": "hub-tok", "node_id": "hub-1", "hub_epoch": 1}),
            encoding="utf-8",
        )
        launcher_web.save_fleet([
            {"id": "node-1", "alias": "192.168.1.85", "host": "192.168.1.85", "port": 18888, "token": "node1-tok", "role": "node"},
        ])

        bad = launcher_web.handle_fleet_announce({"token_sha256": launcher_web._token_digest("unknown"), "host": "192.168.1.200"})
        self.assertFalse(bad["ok"])

        ok = launcher_web.handle_fleet_announce({"token_sha256": launcher_web._token_digest("node1-tok"), "host": "192.168.1.137", "port": 18888})
        self.assertTrue(ok["ok"])
        self.assertEqual(launcher_web.load_fleet()[0]["host"], "192.168.1.137")

    def test_failover_promotes_deterministic_winner_after_threshold(self):
        # Choose token whose sha256 sorts before peer's sha256
        tok_a = "token-alpha-0"
        tok_b = "token-beta-1"
        if launcher_web._token_digest(tok_a) > launcher_web._token_digest(tok_b):
            tok_a, tok_b = tok_b, tok_a

        launcher_web.CONFIG_FILE.write_text(
            json.dumps({"role": "node", "node_token": tok_a, "node_id": "winner-node", "hub_epoch": 0}),
            encoding="utf-8",
        )
        launcher_web.save_fleet([
            {"id": "hub-old", "host": "192.168.1.10", "port": 18888, "token": "dead-hub-tok", "role": "hub"},
            {"id": "peer-b", "host": "192.168.1.20", "port": 18888, "token": tok_b, "role": "node"},
        ])

        def fake_http(host, port, path, token, method="GET", **kwargs):
            if host == "192.168.1.10":
                raise OSError("hub down")
            if host == "192.168.1.20" and path == "/api/role":
                return {"ok": True, "role": "node", "hub_epoch": 0}
            if host == "192.168.1.20" and path == "/api/fleet/sync":
                return {"ok": True}
            raise OSError("unexpected")

        with mock.patch.object(launcher_web, "get_local_ip", return_value="192.168.1.15"), \
             mock.patch.object(launcher_web, "discover_zgx_candidates", return_value=[]), \
             mock.patch.object(launcher_web, "_node_http_json", side_effect=fake_http):
            for _ in range(launcher_web.MASTER_FAILOVER_THRESHOLD - 1):
                res = launcher_web.check_master_and_failover(allow_subnet_scan=False)
                self.assertEqual(launcher_web.load_config()["role"], "node")
                self.assertFalse(res["ok"])
            res = launcher_web.check_master_and_failover(allow_subnet_scan=False)

        self.assertTrue(res.get("promoted"))
        self.assertEqual(launcher_web.load_config()["role"], "hub")
        self.assertEqual(launcher_web.load_config()["hub_epoch"], 1)
        self.assertEqual(launcher_web.load_fleet()[0]["role"], "node")

    def test_split_brain_hub_steps_down_to_higher_epoch(self):
        launcher_web.CONFIG_FILE.write_text(
            json.dumps({"role": "hub", "node_token": "old-hub-tok", "node_id": "old-hub", "hub_epoch": 1}),
            encoding="utf-8",
        )
        launcher_web.save_fleet([
            {"id": "new-hub", "host": "192.168.1.122", "port": 18888, "token": "new-hub-tok", "role": "node"},
        ])

        def fake_http(host, port, path, token, **kwargs):
            if host == "192.168.1.122" and path == "/api/role":
                return {"ok": True, "role": "hub", "hub_epoch": 3}
            if host == "192.168.1.122" and path == "/api/fleet/announce":
                return {"ok": True}
            raise OSError("unreachable")

        with mock.patch.object(launcher_web, "get_local_ip", return_value="192.168.1.113"), \
             mock.patch.object(launcher_web, "_node_http_json", side_effect=fake_http):
            res = launcher_web.check_master_and_failover(allow_subnet_scan=False)

        self.assertEqual(res.get("stepped_down_to"), "192.168.1.122")
        self.assertEqual(launcher_web.load_config()["role"], "node")
        self.assertEqual(launcher_web.load_fleet()[0]["role"], "hub")

    def test_apply_fleet_sync_replicates_ssh_credentials_and_token_rates(self):
        orig_ssh = launcher_web.SSH_CREDENTIALS_FILE
        orig_tok = launcher_web.TOKEN_LEDGER_FILE
        launcher_web.SSH_CREDENTIALS_FILE = self.root / "ssh-credentials.json"
        launcher_web.TOKEN_LEDGER_FILE = self.root / "token_ledger.json"
        try:
            launcher_web.CONFIG_FILE.write_text(
                json.dumps({"role": "node", "node_token": "n-tok", "node_id": "n-1", "hub_epoch": 0}),
                encoding="utf-8",
            )
            res = launcher_web.apply_fleet_sync({
                "peers": [{"id": "hub-1", "host": "192.168.1.122", "port": 18888, "token": "h-tok", "role": "hub"}],
                "ssh_credentials": {'["", 0, "nvidia"]': "Demo4NVDA!!!"},
                "token_rates": {"input_eur_per_1m": 3.5, "output_eur_per_1m": 15.0},
            })
            self.assertTrue(res["ok"])
            self.assertEqual(launcher_web._load_ssh_credentials()['["", 0, "nvidia"]'], "Demo4NVDA!!!")
            ledger = json.loads(launcher_web.TOKEN_LEDGER_FILE.read_text(encoding="utf-8"))
            self.assertEqual(ledger["rates"]["input_eur_per_1m"], 3.5)
        finally:
            launcher_web.SSH_CREDENTIALS_FILE = orig_ssh
            launcher_web.TOKEN_LEDGER_FILE = orig_tok

    def test_set_network_mode_static_and_dhcp(self):
        conn = {"name": "Wired", "type": "802-3-ethernet", "device": "eth0", "uuid": "uuid-123"}
        ok_proc = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(launcher_web, "_get_active_nm_connection", return_value=conn), \
             mock.patch.object(launcher_web, "_default_gateway_and_cidr", return_value=("192.168.1.1", "192.168.1.104/24")), \
             mock.patch.object(launcher_web, "_run_nmcli", return_value=ok_proc) as run_nm, \
             mock.patch.object(launcher_web.threading, "Thread") as th:
            res_static = launcher_web.set_network_mode_action({"mode": "static", "ip_cidr": "192.168.1.104/24"})
            self.assertTrue(res_static["ok"])
            self.assertEqual(res_static["mode"], "static")
            self.assertEqual(res_static["gateway"], "192.168.1.1")
            th.return_value.start.assert_called()

            res_dhcp = launcher_web.set_network_mode_action({"mode": "dhcp"})
            self.assertTrue(res_dhcp["ok"])
            self.assertEqual(res_dhcp["mode"], "dhcp")
            self.assertGreaterEqual(run_nm.call_count, 2)

    def test_start_all_nano_collective_starts_nanos_and_skips_fury(self):
        launcher_web.CONFIG_FILE.write_text(
            json.dumps({"role": "hub", "node_token": "hub-tok", "node_id": "hub-1", "hub_epoch": 1}),
            encoding="utf-8",
        )
        launcher_web.save_fleet([
            {"id": "nano-1", "alias": "nano-1", "hw_model": "ZGX-NANO", "host": "10.0.0.11", "port": 18888, "token": "t1", "role": "node"},
            {"id": "fury-2", "alias": "fury-2", "hw_model": "ZGX-FURY", "host": "10.0.0.12", "port": 18888, "token": "t2", "role": "node"},
        ])
        called = []

        def fake_http(host, port, path, token, method="GET", **kwargs):
            called.append((host, path))
            return {"ok": True}

        with mock.patch.object(launcher_web, "get_local_ip", return_value="10.0.0.10"), \
             mock.patch.object(launcher_web, "get_status", return_value={"system": {"hw": {"hw_model": "ZGX-FURY"}}}), \
             mock.patch.object(launcher_web, "_node_http_json", side_effect=fake_http):
            res = launcher_web.start_all_nano_collective_action()

        self.assertTrue(res["ok"])
        self.assertEqual(res["started"], ["nano-1"])
        self.assertEqual(res["failed"], [])
        self.assertEqual(called, [("10.0.0.11", "/api/start/nano-collective")])


if __name__ == "__main__":
    unittest.main()
