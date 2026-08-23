import unittest
from unittest.mock import patch

from app.services.marketplace import browser
from app.services.marketplace.extension_bridge import ExtensionBridgeError


def tab(tab_id: str, url: str, tab_type: str = "page") -> dict:
    return {"id": tab_id, "url": url, "type": tab_type}


class BrowserTabSelectionTests(unittest.TestCase):
    def test_extension_actions_wait_through_one_alarm_wake_cycle(self):
        with patch.object(browser.settings, "BROWSER_MODE", "extension"), patch.object(
            browser, "browser_ready", return_value=False
        ), patch.object(browser, "ensure_browser"), patch.object(
            browser,
            "extension_request",
            side_effect=[[], {"id": "new", "url": "https://shopee.com.my/"}, True],
        ) as request:
            self.assertEqual(browser.list_tabs(), [])
            self.assertEqual(browser.new_tab("https://shopee.com.my/")["id"], "new")
            self.assertTrue(browser.activate_tab("new"))

        self.assertGreaterEqual(browser.EXTENSION_ACTION_TIMEOUT_SECONDS, 30)
        self.assertEqual(
            [item.kwargs["timeout"] for item in request.call_args_list],
            [browser.EXTENSION_ACTION_TIMEOUT_SECONDS] * 3,
        )

    def test_ensure_browser_wakes_a_sleeping_mv3_worker(self):
        with patch.object(browser.settings, "BROWSER_MODE", "extension"), patch.object(
            browser, "extension_ready", return_value=False
        ), patch.object(browser, "extension_request", return_value=[]) as request:
            browser.ensure_browser()

        request.assert_called_once_with(
            "tabs", timeout=browser.EXTENSION_ACTION_TIMEOUT_SECONDS
        )

    def test_normal_collection_prefers_search_tab_over_home_or_challenge(self):
        tabs = [
            tab("home", "https://shopee.com.my/"),
            tab("verify", "https://shopee.xiapibuy.com/verify?from=search"),
            tab("search", "https://shopee.com.my/search?keyword=bottle"),
        ]
        with patch.object(browser, "list_tabs", return_value=tabs):
            selected = browser.find_platform_tab("shopee", prefer_verification=False)
        self.assertEqual(selected["id"], "search")

    def test_verification_path_prefers_challenge_tab(self):
        tabs = [
            tab("home", "https://shopee.com.my/"),
            tab("search", "https://shopee.com.my/search?keyword=bottle"),
            tab("verify", "https://shopee.xiapibuy.com/verify?from=search"),
        ]
        with patch.object(browser, "list_tabs", return_value=tabs):
            selected = browser.find_platform_tab("shopee")
        self.assertEqual(selected["id"], "verify")

    def test_lazada_security_host_is_preferred_for_verification(self):
        tabs = [
            tab("catalog", "https://www.lazada.com.my/catalog/?q=bottle"),
            tab("security", "https://acs-m.lazada.com.my/security-check?continue=1"),
        ]
        with patch.object(browser, "list_tabs", return_value=tabs):
            self.assertEqual(browser.find_platform_tab("lazada")["id"], "security")
            self.assertEqual(
                browser.find_platform_tab("lazada", prefer_verification=False)["id"],
                "catalog",
            )

    def test_platform_hostname_must_be_real_host_not_query_text(self):
        tabs = [
            tab("evil", "https://example.test/?next=https://shopee.com.my/verify"),
            tab("extension", "chrome-extension://abc/page.html?site=shopee.com.my"),
        ]
        with patch.object(browser, "list_tabs", return_value=tabs):
            self.assertIsNone(browser.find_platform_tab("shopee"))

    def test_ensure_platform_tab_uses_search_candidate_not_verification_candidate(self):
        tabs = [
            tab("verify", "https://shopee.xiapibuy.com/verify"),
            tab("search", "https://shopee.com.my/search?keyword=old"),
        ]
        with patch.object(browser, "ensure_browser"), patch.object(
            browser, "list_tabs", return_value=tabs
        ), patch.object(browser, "new_tab") as new_tab:
            selected = browser.ensure_platform_tab(
                "shopee",
                "https://shopee.com.my/search?keyword=new",
            )
        self.assertEqual(selected["id"], "search")
        new_tab.assert_not_called()

    def test_activate_platform_tab_activates_same_verification_candidate(self):
        tabs = [
            tab("search", "https://shopee.com.my/search?keyword=bottle"),
            tab("verify", "https://shopee.xiapibuy.com/verify"),
        ]
        with patch.object(browser, "list_tabs", return_value=tabs), patch.object(
            browser, "activate_tab", return_value=True
        ) as activate:
            self.assertTrue(browser.activate_platform_tab("shopee"))
        activate.assert_called_once_with("verify")

    def test_same_priority_verification_tabs_do_not_depend_on_json_list_order(self):
        first = tab("challenge-a", "https://shopee.xiapibuy.com/verify?a=1")
        second = tab("challenge-z", "https://shopee.xiapibuy.com/verify?b=2")
        with patch.object(browser, "list_tabs", return_value=[first, second]):
            selected_a = browser.find_platform_tab("shopee")
        with patch.object(browser, "list_tabs", return_value=[second, first]):
            selected_b = browser.find_platform_tab("shopee")
        self.assertEqual(selected_a["id"], "challenge-z")
        self.assertEqual(selected_b["id"], "challenge-z")

    def test_platform_tab_id_lookup_still_validates_real_hostname(self):
        tabs = [
            tab("wanted", "https://shopee.xiapibuy.com/verify?run=1"),
            tab("wrong-host", "https://example.test/verify"),
        ]
        with patch.object(browser, "list_tabs", return_value=tabs):
            self.assertEqual(
                browser.find_platform_tab_by_id("shopee", "wanted")["id"],
                "wanted",
            )
            self.assertIsNone(
                browser.find_platform_tab_by_id("shopee", "wrong-host")
            )

    def test_extension_lock_selects_the_current_run_tab_among_multiple_challenges(self):
        locked = tab("challenge-current", "https://shopee.xiapibuy.com/verify?run=current")
        with patch.object(browser.settings, "BROWSER_MODE", "extension"), patch.object(
            browser,
            "extension_request",
            return_value=locked,
        ) as request:
            selected = browser.activate_locked_platform_tab("shopee", "run:17:shopee")

        self.assertEqual(selected, locked)
        request.assert_called_once_with(
            "activate_locked_platform",
            timeout=browser.LOCKED_TAB_ACTION_TIMEOUT_SECONDS,
            platform="shopee",
            lock_key="run:17:shopee",
        )

    def test_old_extension_unknown_lock_action_falls_back_cleanly(self):
        with patch.object(browser.settings, "BROWSER_MODE", "extension"), patch.object(
            browser,
            "extension_request",
            side_effect=ExtensionBridgeError("Unknown bridge action"),
        ):
            self.assertIsNone(
                browser.activate_locked_platform_tab("lazada", "run:18:lazada")
            )

    def test_release_run_lock_is_bounded_and_old_extension_compatible(self):
        with patch.object(browser.settings, "BROWSER_MODE", "extension"), patch.object(
            browser,
            "extension_request",
            return_value=True,
        ) as request:
            self.assertTrue(
                browser.release_platform_tab_lock(
                    "shopee",
                    "run:19:shopee",
                    "owner-19",
                )
            )
        request.assert_called_once_with(
            "release_platform_lock",
            timeout=browser.LOCKED_TAB_ACTION_TIMEOUT_SECONDS,
            platform="shopee",
            lock_key="run:19:shopee",
            lock_owner="owner-19",
        )


if __name__ == "__main__":
    unittest.main()
