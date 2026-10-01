from datetime import date

from django.urls import reverse

from .models import Account, Transaction
from .test_phase13 import LoggedInTestCase


class SaveAndAddAnotherTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.account = Account.objects.create(name="Checking")

    def _data(self, **extra):
        return {
            "account": self.account.pk,
            "date": "01/03/2026",
            "payee_name": "Shop",
            "category": "",
            "memo": "",
            "amount": "-10.00",
            **extra,
        }

    def test_new_form_shows_both_buttons(self):
        response = self.client.get(reverse("finance:transaction_add"))
        self.assertContains(response, "Save and add another")

    def test_save_goes_to_all_transactions(self):
        response = self.client.post(reverse("finance:transaction_add"), self._data())
        self.assertRedirects(response, reverse("finance:transaction_list"))
        self.assertEqual(Transaction.objects.count(), 1)

    def test_save_and_add_another_returns_to_blank_form_on_same_account(self):
        response = self.client.post(reverse("finance:transaction_add"), self._data(save_and_add="1"))
        self.assertRedirects(
            response,
            reverse("finance:transaction_add") + f"?account={self.account.pk}&date=2026-03-01",
        )
        self.assertEqual(Transaction.objects.count(), 1)

    def test_edit_form_has_no_add_another_button(self):
        txn = Transaction.objects.create(account=self.account, date=date(2026, 3, 1), amount=-5)
        response = self.client.get(reverse("finance:transaction_edit", args=[txn.pk]))
        self.assertNotContains(response, "Save and add another")

    def test_blank_form_prefills_the_date_from_the_previous_save(self):
        response = self.client.get(reverse("finance:transaction_add"), {"date": "2026-03-01"})
        self.assertEqual(response.context["form"].initial["date"], date(2026, 3, 1))

    def test_bad_date_in_url_falls_back_to_today(self):
        response = self.client.get(reverse("finance:transaction_add"), {"date": "garbage"})
        self.assertEqual(response.context["form"].initial["date"], date.today())
