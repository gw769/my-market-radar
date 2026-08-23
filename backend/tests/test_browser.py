import unittest
from unittest.mock import patch

from app.services.marketplace import browser


def tab(tab_id: str, url: str, tab_type: str = "page") -> dict:
    return {"id": tab_id, "url": url, "type": tab_type}


class BrowserTabSelectionTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
