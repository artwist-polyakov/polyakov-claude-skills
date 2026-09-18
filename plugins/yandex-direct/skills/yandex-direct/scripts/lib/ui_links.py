"""Ссылки на кабинет и кампанию Директа; сеть и конфигурация не нужны."""

from urllib.parse import urlencode

from config import DirectFailure
from incoming import whole_number


def _login(login) -> str:
    if not isinstance(login, str) or not login.strip():
        raise DirectFailure("Для ссылки нужен логин рекламного кабинета.")
    return login.strip()


def account_url(login) -> str:
    """Кампании кабинета, кроме архивных; статистика за последние 30 дней."""
    query = urlencode({"ulogin": _login(login), "filter-reset": "true",
                       "stat-preset": "last30Days", "dim-filter": "CPC_AND_CPM",
                       "status-filter": "ALL_EXCEPT_ARCHIVED"})
    return f"https://direct.yandex.ru/dna/grid/campaigns?{query}"


def campaign_url(login, campaign_id, *, edit=False) -> str:
    """Открыть группы кампании либо её настройки по логину и числовому ID."""
    login = _login(login)
    campaign_id = whole_number(campaign_id, "ID кампании")
    if campaign_id < 1:
        raise DirectFailure("Для ссылки нужен положительный ID кампании.")
    path = "campaigns-edit" if edit else "grid/groups"
    query = urlencode({"ulogin": login, "campaigns-ids": campaign_id})
    return f"https://direct.yandex.ru/dna/{path}?{query}"
