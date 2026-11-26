"""The one "does this link stay on this site?" check, which the
middleware and the views share."""

from django.test import RequestFactory, SimpleTestCase

from ..same_site import stays_on_this_site


class StaysOnThisSiteTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_a_path_on_this_site_stays(self):
        request = self.factory.get("/")
        for url in ("/transactions/", "/transactions/?account=3", "/"):
            with self.subTest(url=url):
                self.assertTrue(stays_on_this_site(request, url))

    def test_a_link_to_another_site_does_not(self):
        request = self.factory.get("/")
        for url in (
            "//evil.example",
            "/\\evil.example",
            "http://evil.example/",
            "https://evil.example",
            "javascript:alert(1)",
        ):
            with self.subTest(url=url):
                self.assertFalse(stays_on_this_site(request, url))

    def test_a_full_address_on_this_host_stays_but_only_over_https_on_an_https_request(self):
        plain = self.factory.get("/", HTTP_HOST="stashy.localhost")
        secure = self.factory.get("/", HTTP_HOST="stashy.localhost", secure=True)
        self.assertTrue(stays_on_this_site(plain, "http://stashy.localhost/x/"))
        self.assertFalse(stays_on_this_site(secure, "http://stashy.localhost/x/"))
        self.assertTrue(stays_on_this_site(secure, "https://stashy.localhost/x/"))
