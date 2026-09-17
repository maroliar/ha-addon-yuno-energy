"""
Yuno Energy addon.

Logs into the Yuno Energy API (see yuno_client.py for the reverse-engineered
auth flow), fetches account billing/usage/vampire-energy/bill data, and
publishes it as sensors via Home Assistant MQTT discovery.

Yuno's own smart meter data is only refreshed once a day (confirmed via
their public FAQ: "Half hour data is obtained remotely from your ESB
Networks smart meter daily"), so polling more often than that just repeats
the same data and adds unnecessary load - update_interval_minutes defaults
to 1440 (once a day) and shouldn't normally need to go lower.
"""

import json
import sys
import time
from datetime import datetime

from paho.mqtt.publish import multiple as mqtt_publish_multiple

from yuno_client import YunoClient, YunoLoginError, YunoNoActiveAccountError

OPTIONS_PATH = "/data/options.json"

DEVICE_INFO = {
    "identifiers": ["yuno_energy_addon"],
    "name": "Yuno Energy",
    "manufacturer": "maroliar",
}


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%d/%m/%Y - %H:%M:%S')}] {msg}", flush=True)


def load_options() -> dict:
    with open(OPTIONS_PATH, encoding="utf-8") as f:
        return json.load(f)


def discovery_topic(object_id: str) -> str:
    return f"homeassistant/sensor/yuno_energy_{object_id}/config"


def state_topic(object_id: str) -> str:
    return f"yuno_energy/{object_id}/state"


def attributes_topic(object_id: str) -> str:
    return f"yuno_energy/{object_id}/attributes"


def sensor_messages(
    object_id: str,
    name: str,
    state,
    attributes: dict,
    *,
    unit: str | None = None,
    device_class: str | None = None,
    state_class: str | None = None,
    icon: str | None = None,
) -> list:
    discovery_payload = {
        "name": name,
        "unique_id": f"yuno_energy_{object_id}",
        "default_entity_id": f"sensor.yuno_{object_id}",
        "state_topic": state_topic(object_id),
        "json_attributes_topic": attributes_topic(object_id),
        "device": DEVICE_INFO,
    }
    if unit:
        discovery_payload["unit_of_measurement"] = unit
    if device_class:
        discovery_payload["device_class"] = device_class
    if state_class:
        discovery_payload["state_class"] = state_class
    if icon:
        discovery_payload["icon"] = icon

    return [
        {"topic": discovery_topic(object_id), "payload": json.dumps(discovery_payload),
         "qos": 1, "retain": True},
        {"topic": state_topic(object_id), "payload": state, "qos": 1, "retain": True},
        {"topic": attributes_topic(object_id), "payload": json.dumps(attributes, ensure_ascii=False),
         "qos": 1, "retain": True},
    ]


def build_messages(client: YunoClient) -> list:
    billing = client.get_account_billing_details()
    usage = client.get_electricity_usage()
    vampire = client.get_vampire_energy()
    bills = client.get_bill_list()

    # Yuno keeps the login itself working even after an account loses its
    # active electricity plan (e.g. switching to another supplier) - it just
    # starts returning an empty body (null) for account-specific endpoints
    # instead of an error, so that has to be checked explicitly here.
    missing = [name for name, value in (
        ("billing", billing), ("usage", usage), ("vampire", vampire),
    ) if value is None]
    if missing:
        raise YunoNoActiveAccountError(
            f"Yuno's API accepted the login but returned no data for: {', '.join(missing)}. "
            "This usually means the account no longer has an active electricity plan with Yuno."
        )

    # Freshness fingerprint: Yuno only refreshes this data once a day (see
    # README's Known limitations). Returned alongside the messages so the
    # caller can diff it against the previous poll and flag exactly when
    # that daily refresh actually happens, without guessing from Yuno's own
    # (unpublished) internal schedule.
    latest_day = (usage.get("dailyUsageDetails") or [{}])[0]
    freshness = (
        latest_day.get("date"),
        latest_day.get("dailyUsageInkWh"),
        billing.get("estimatedBillCurrentPeriodLastUpdateDate"),
    )

    messages = []

    messages += sensor_messages(
        "forecast", "Yuno Forecast", billing["estimatedBillCurrentPeriodInEuro"],
        {
            "forecast_kwh": float(billing["estimatedBillCurrentPeriodInkWh"]),
            "prediction_euro": billing["estimatedBillPeriodStartInEuro"],
            "prediction_kwh": float(billing["estimatedBillPeriodStartInkWh"]),
            "next_period_forecast_euro": billing["estimatedBillNextPeriodEndInEuro"],
            "next_period_forecast_kwh": float(billing["estimatedBillNextPeriodEndInKWh"]),
            "beat_the_bill_euro": billing["beatTheBillInEuro"],
            "beat_the_bill_kwh": float(billing["beatTheBillInkWh"]),
            "current_billing_period_start": billing["currentBillingPeriodStartDate"],
            "current_billing_period_end": billing["currentBillingPeriodEndDate"],
            "next_billing_period_start": billing["nextBillingPeriodStartDate"],
            "next_billing_period_end": billing["nextBillingPeriodEndDate"],
            "last_update_date": billing["estimatedBillCurrentPeriodLastUpdateDate"],
        },
        unit="EUR", device_class="monetary", icon="mdi:cash-clock",
    )

    messages += sensor_messages(
        "next_payment", "Yuno Next Payment", billing["nextPaymentEstimateInEuro"],
        {"next_payment_date": billing["nextPaymentDate"]},
        unit="EUR", device_class="monetary", icon="mdi:calendar-cash",
    )

    # dailyUsageDetails is newest-first; [0] is the most recent day Yuno has
    # data for, which per their own FAQ is always yesterday (D+1 lag from
    # the smart meter), never "today".
    daily = usage.get("dailyUsageDetails") or []
    latest_day = daily[0] if daily else None
    messages += sensor_messages(
        "usage_latest_day", "Yuno Usage (Latest Day)",
        latest_day["dailyUsageInkWh"] if latest_day else None,
        {
            "date": latest_day["date"] if latest_day else None,
            "usage_euro": latest_day["dailyUsageInEuro"] if latest_day else None,
            "standing_charge_euro": latest_day["dailyStandingChargeInEuro"] if latest_day else None,
            "hourly_kwh": (usage.get("hourlyUsageDetails") or [{}])[0].get("hourlyUsageInKwh"),
        },
        unit="kWh", device_class="energy", state_class="total", icon="mdi:lightning-bolt",
    )

    annual_ve = vampire.get("annualData", {}).get("annualVEUsageInEuro")
    daily_ve = vampire.get("dailyData", {}) or {}
    monthly_ve = vampire.get("monthlyData", {}) or {}
    messages += sensor_messages(
        "vampire_energy", "Yuno Vampire Energy (Annual)", annual_ve,
        {
            "daily_date": daily_ve.get("date"),
            "daily_kwh": daily_ve.get("vampireEnergyHourbyDayInkWh"),
            "monthly_comparison_euro": monthly_ve.get("monthlyComparisonInEuro"),
        },
        unit="EUR", device_class="monetary", icon="mdi:ghost",
    )

    latest_bill = bills[0] if bills else None
    messages += sensor_messages(
        "last_bill", "Yuno Last Bill",
        latest_bill["billTotalInEUR"] if latest_bill else None,
        {
            "status": latest_bill["billStatus"] if latest_bill else None,
            "date_of_bill_creation": latest_bill["dateOfBillCreation"] if latest_bill else None,
            "reference": latest_bill["friendlyName"] if latest_bill else None,
            "invoice_id": latest_bill["invoiceID"] if latest_bill else None,
        },
        unit="EUR", device_class="monetary", icon="mdi:receipt-text",
    )

    return messages, freshness


def fetch_and_publish(email: str, password: str, app_credential: str, mqtt_config: dict, last_freshness) -> tuple:
    client = YunoClient(email, password, app_credential)
    log("Logging in...")
    client.login()

    messages, freshness = build_messages(client)
    date, kwh, last_update = freshness

    if last_freshness is None:
        log(f"Freshness check - latest usage day: {date} ({kwh} kWh) | "
            f"forecast last updated: {last_update}")
    elif freshness != last_freshness:
        log(f"*** DATA UPDATED *** latest usage day: {date} ({kwh} kWh) | "
            f"forecast last updated: {last_update} "
            f"(previous: {last_freshness[0]} / {last_freshness[1]} kWh / {last_freshness[2]})")
    else:
        log(f"Freshness check - unchanged since last poll "
            f"(latest usage day: {date}, {kwh} kWh)")

    auth = {"username": mqtt_config.get("user", ""), "password": mqtt_config.get("pass", "")}
    mqtt_publish_multiple(
        messages,
        hostname=mqtt_config["host"],
        port=mqtt_config["port"],
        auth=auth,
        client_id="yuno_energy",
    )
    log(f"Published {len(messages) // 3} sensor(s) to MQTT (retained).")
    return freshness


def main() -> None:
    options = load_options()
    email = options.get("yuno_email", "")
    password = options.get("yuno_password", "")
    app_credential = options.get("yuno_app_credential", "")
    interval_minutes = options.get("update_interval_minutes", 1440)
    mqtt_config = options.get("mqtt", {})

    if not email or not password:
        log("ERROR: yuno_email / yuno_password not configured. "
            "Fill them in on the addon's Configuration tab.")
        sys.exit(1)

    if not app_credential:
        log("ERROR: yuno_app_credential not configured. See "
            "EXTRACTING_CREDENTIAL.md for how to get this value, then fill "
            "it in on the addon's Configuration tab.")
        sys.exit(1)

    if not mqtt_config.get("host"):
        log("ERROR: mqtt.host not configured. Fill in the MQTT section on "
            "the addon's Configuration tab (host, port, user, pass).")
        sys.exit(1)

    log(f"Addon started. Interval between queries: {interval_minutes} min.")

    last_freshness = None
    while True:
        try:
            last_freshness = fetch_and_publish(email, password, app_credential, mqtt_config, last_freshness)
        except YunoLoginError as e:
            log(f"LOGIN ERROR: {e}")
        except YunoNoActiveAccountError as e:
            log(f"NO ACTIVE ACCOUNT: {e}")
        except Exception as e:  # noqa: BLE001
            log(f"UNEXPECTED ERROR: {e}")

        log(f"Waiting {interval_minutes} minutes until the next query...")
        time.sleep(interval_minutes * 60)


if __name__ == "__main__":
    main()
