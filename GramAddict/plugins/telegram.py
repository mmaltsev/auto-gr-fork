import json
import logging
from datetime import datetime
from typing import Optional

import requests
import yaml
from colorama import Fore, Style

from GramAddict.core.plugin_loader import Plugin

logger = logging.getLogger(__name__)


def load_sessions(username) -> Optional[dict]:
    try:
        with open(f"accounts/{username}/sessions.json") as json_data:
            return json.load(json_data)
    except FileNotFoundError:
        logger.error("No session data found. Skipping report generation.")
        return None


def load_telegram_config(username) -> Optional[dict]:
    try:
        with open(f"accounts/{username}/telegram.yml", "r", encoding="utf-8") as stream:
            return yaml.safe_load(stream)
    except FileNotFoundError as e:
        logger.error(f"Configuration file not found: {e}")
        return None


def telegram_bot_send_text(bot_api_token, bot_chat_ID, text):
    try:
        method = "sendMessage"
        parse_mode = "markdown"
        params = {"text": text, "chat_id": bot_chat_ID, "parse_mode": parse_mode}
        url = f"https://api.telegram.org/bot{bot_api_token}/{method}"
        return requests.get(url, params=params).json()
    except Exception as e:
        logger.error(f"Error sending Telegram message: {e}")
        return None


def _initialize_aggregated_data():
    return {
        "total_likes": 0,
        "total_watched": 0,
        "total_followed": 0,
        "total_unfollowed": 0,
        "total_comments": 0,
        "total_pm": 0,
        "duration": 0,
        "followers": float("inf"),
        "following": float("inf"),
        "followers_gained": 0,
        "total_scraped": [],
    }


def _calculate_session_duration(session):
    try:
        start_datetime = datetime.strptime(
            session["start_time"], "%Y-%m-%d %H:%M:%S.%f"
        )
        finish_datetime = datetime.strptime(
            session["finish_time"], "%Y-%m-%d %H:%M:%S.%f"
        )
        return int((finish_datetime - start_datetime).total_seconds() / 60)
    except ValueError:
        logger.debug(
            f"{session['id']} has no finish_time. Skipping duration calculation."
        )
        return 0


def daily_summary(sessions):
    daily_aggregated_data = {}
    for session in sessions:
        date = session["start_time"][:10]
        daily_aggregated_data.setdefault(date, _initialize_aggregated_data())
        duration = _calculate_session_duration(session)
        daily_aggregated_data[date]["duration"] += duration

        for key in [
            "total_likes",
            "total_watched",
            "total_followed",
            "total_unfollowed",
            "total_comments",
            "total_pm",
        ]:
            daily_aggregated_data[date][key] += session.get(key, 0)

        account_list = list(session.get("total_scraped", 0))
        daily_aggregated_data[date]["total_scraped"].extend(x for x in account_list if x not in daily_aggregated_data[date]["total_scraped"])

        daily_aggregated_data[date]["followers"] = min(
            session.get("profile", {}).get("followers", 0),
            daily_aggregated_data[date]["followers"],
        )
        daily_aggregated_data[date]["following"] = min(
            session.get("profile", {}).get("following", 0),
            daily_aggregated_data[date]["following"],
        )
    return _calculate_followers_gained(daily_aggregated_data)


def _calculate_followers_gained(aggregated_data):
    dates_sorted = sorted(aggregated_data.keys())
    previous_followers = None
    for date in dates_sorted:
        current_followers = aggregated_data[date]["followers"]
        if previous_followers is not None:
            followers_gained = current_followers - previous_followers
            aggregated_data[date]["followers_gained"] = followers_gained
        previous_followers = current_followers
    return aggregated_data


def generate_report(
    username,
    last_session,
    daily_aggregated_data,
    weekly_average_data,
    followers_now,
    following_now,
    is_last_session_of_today,
    is_crash_report,
):
    if is_crash_report:
        return "💔 the bot has just crashed. Need help!"
    return f"""
*📅 Today's total actions*
• {daily_aggregated_data["duration"]} minutes of botting
• accounts scraped: {daily_aggregated_data["total_scraped"]}
• {daily_aggregated_data["total_likes"]} likes
• {daily_aggregated_data["total_watched"]} stories watched

*🗓 7-Day Average*
• {weekly_average_data["duration"] / 7:.0f} minutes of botting
• {weekly_average_data["total_likes"] / 7:.0f} likes
• {weekly_average_data["total_watched"] / 7:.0f} stories watched
    """


def weekly_average(daily_aggregated_data, today) -> dict:
    weekly_average_data = _initialize_aggregated_data()

    for date in daily_aggregated_data:
        if (today - datetime.strptime(date, "%Y-%m-%d")).days > 7:
            continue
        for key in [
            "total_likes",
            "total_watched",
            "total_followed",
            "total_unfollowed",
            "total_comments",
            "total_pm",
            "duration",
            "followers_gained",
        ]:
            weekly_average_data[key] += daily_aggregated_data[date][key]
    return weekly_average_data


class TelegramReports(Plugin):
    """Generate reports at the end of the session and send them using telegram"""

    def __init__(self):
        super().__init__()
        self.description = "Generate reports at the end of the session and send them using telegram. You have to configure 'telegram.yml' in your account folder"
        self.arguments = [
            {
                "arg": "--telegram-reports",
                "help": "at the end of every session send a report to your telegram account",
                "action": "store_true",
                "operation": True,
            }
        ]

    def run(self, config, plugin, followers_now, following_now, time_left):
        username = config.args.username
        working_hours = config.args.working_hours
        if username is None:
            logger.error("You have to specify a username for getting reports!")
            return

        sessions = load_sessions(username)
        if not sessions:
            logger.error(
                f"No session data found for {username}. Skipping report generation."
            )
            return

        last_session = sessions[-1]
        last_session["duration"] = _calculate_session_duration(last_session)

        telegram_config = load_telegram_config(username)
        if not telegram_config:
            logger.error(
                f"No telegram configuration found for {username}. Skipping report generation."
            )
            return

        daily_aggregated_data = daily_summary(sessions)
        today_data = daily_aggregated_data.get(last_session["start_time"][:10], {})
        today = datetime.now()
        last_session_start = working_hours[-1].split('-')[0]
        last_session_start_hour = int(last_session_start.split('.')[0])
        last_session_start_minute = int(last_session_start.split('.')[1])
        last_session_start_datetime = today.replace(hour=last_session_start_hour, minute=last_session_start_minute, second=0, microsecond=0)
        is_last_session_of_today = today > last_session_start_datetime
        weekly_average_data = weekly_average(daily_aggregated_data, today)
        is_crash_report = False
        if followers_now == 0 and following_now == 0 and time_left == 0:
            is_crash_report = True
        if (is_last_session_of_today or is_crash_report):
            report = generate_report(
                username,
                last_session,
                today_data,
                weekly_average_data,
                followers_now,
                following_now,
                is_last_session_of_today,
                is_crash_report,
            )
            response = telegram_bot_send_text(
                telegram_config.get("telegram-api-token"),
                telegram_config.get("telegram-chat-id"),
                report,
            )
            if response and response.get("ok"):
                logger.info(
                    "Telegram message sent successfully.",
                    extra={"color": f"{Style.BRIGHT}{Fore.BLUE}"},
                )
            else:
                error = response.get("description") if response else "Unknown error"
                logger.error(f"Failed to send Telegram message: {error}")
