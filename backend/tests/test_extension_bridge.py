import threading
import unittest

from app.services.marketplace.extension_bridge import (
    EXTENSION_ID,
    EXTENSION_VERSION,
    ExtensionBridge,
    ExtensionBridgeError,
    extension_update_manifest,
    is_trusted_extension_request,
)


class ExtensionBridgeTests(unittest.TestCase):
    def test_update_manifest_uses_selected_distribution_base(self):
        xml = extension_update_manifest("https://market.example/browser-extension/").decode()
        self.assertIn(f'appid="{EXTENSION_ID}"', xml)
        self.assertIn(f'version="{EXTENSION_VERSION}"', xml)
        self.assertIn(
            'codebase="https://market.example/browser-extension/chrome-extension.crx"',
            xml,
        )

    def test_only_extension_style_fetch_can_dequeue_commands(self):
        self.assertTrue(is_trusted_extension_request({"Sec-Fetch-Site": "none"}))
        self.assertFalse(is_trusted_extension_request({"Sec-Fetch-Site": "cross-site"}))
        self.assertFalse(is_trusted_extension_request({}))

    def test_command_round_trip(self):
        bridge = ExtensionBridge()
        bridge.heartbeat()
        outcome = {}

        def requester():
            outcome["value"] = bridge.request("tabs", timeout=2, platform="lazada")

        thread = threading.Thread(target=requester)
        thread.start()
        command = bridge.next_command(1)
        self.assertEqual(command["action"], "tabs")
        self.assertEqual(command["params"]["platform"], "lazada")
        self.assertTrue(bridge.complete(command["id"], result=[{"id": "7"}]))
        thread.join(2)
        self.assertEqual(outcome["value"], [{"id": "7"}])

    def test_command_error_is_reported(self):
        bridge = ExtensionBridge()
        bridge.heartbeat()
        outcome = {}

        def requester():
            try:
                bridge.request("attach", timeout=2)
            except Exception as exc:  # noqa: BLE001 - asserted below
                outcome["error"] = exc

        thread = threading.Thread(target=requester)
        thread.start()
        command = bridge.next_command(1)
        bridge.complete(command["id"], error="tab unavailable")
        thread.join(2)
        self.assertIsInstance(outcome["error"], ExtensionBridgeError)
        self.assertIn("tab unavailable", str(outcome["error"]))

    def test_timeout_removes_stale_command(self):
        bridge = ExtensionBridge()
        bridge.heartbeat()
        with self.assertRaises(ExtensionBridgeError):
            bridge.request("tabs", timeout=0.01)
        self.assertIsNone(bridge.next_command(0))


if __name__ == "__main__":
    unittest.main()
