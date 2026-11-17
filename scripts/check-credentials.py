"""Report configuration; optionally verify SearchApi access and quota."""

import argparse

import httpx

from cpfc_trip.config import Settings


def check_live(settings: Settings) -> bool:
    try:
        if not settings.searchapi_enabled or not settings.searchapi_api_key.get_secret_value():
            raise ValueError("SearchApi unavailable")
        response = httpx.get(
            "https://www.searchapi.io/api/v1/me",
            headers={"Authorization": f"Bearer {settings.searchapi_api_key.get_secret_value()}"},
            timeout=20,
        )
        response.raise_for_status()
        account = response.json()["account"]
        used, allowance, credits = (
            int(account[key])
            for key in ("current_month_usage", "monthly_allowance", "remaining_credits")
        )
        available = max(allowance - used, credits)
        print(f"SearchApi account: {used}/{allowance} monthly requests used; {credits} credits")
        if available <= 0:
            print("SearchApi quota: exhausted; review the plan before running a paid validation")
            return False
    except Exception:
        # Do not print SDK exceptions: these can contain credential response bodies.
        print("SearchApi account check: failed; check the key, plan and network")
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-live", action="store_true", help="Read SearchApi quota; no paid searches"
    )
    args = parser.parse_args()
    settings = Settings()
    print(
        f"SearchApi: {'configured' if settings.searchapi_api_key.get_secret_value() else 'missing'}"
    )
    print(f"Planning mode: {settings.planner_mode}")
    if args.check_live and not check_live(settings):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
