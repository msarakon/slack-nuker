#!/usr/bin/env python3
"""Preview or delete a user's messages in one Slack conversation.

Set SLACK_TOKEN to a Slack user token with chat:write and the appropriate
conversation history scope. Run with --help for usage.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

REQUEST_INTERVAL_SECONDS = 1.2
PAGE_SIZE = 200
_last_request_time = None


class SlackError(Exception):
    pass


def slack_call(token, method, parameters=None, *, write=False):
    global _last_request_time

    encoded = urllib.parse.urlencode(parameters or {}).encode("utf-8")
    url = f"https://slack.com/api/{method}"
    if not write and encoded:
        url += "?" + encoded.decode("ascii")
    headers = {"Authorization": f"Bearer {token}"}
    if write:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(
        url, data=encoded if write else None, headers=headers
    )
    for attempt in range(11):
        if _last_request_time is not None:
            delay = REQUEST_INTERVAL_SECONDS - (time.monotonic() - _last_request_time)
            if delay > 0:
                time.sleep(delay)
        _last_request_time = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 429 and attempt < 10:
                try:
                    delay = max(1, int(error.headers.get("Retry-After", "1")))
                except ValueError:
                    raise SlackError("Invalid Slack Retry-After header") from error
                print(f"Rate limited; retrying in {delay} seconds.", file=sys.stderr)
                time.sleep(delay)
                continue
            raise SlackError(f"{method}: HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise SlackError(f"{method}: network error: {error}") from error
        except (ValueError, UnicodeDecodeError) as error:
            raise SlackError(f"{method}: invalid JSON response") from error
        if not isinstance(result, dict):
            raise SlackError(f"{method}: unexpected response")
        if not result.get("ok"):
            raise SlackError(f"{method}: {result.get('error', 'unknown Slack error')}")
        return result
    raise SlackError(f"{method}: too many rate-limit retries")


class MessageBudget:
    def __init__(self, maximum):
        self.maximum = maximum
        self.fetched = 0
        self.truncated = False


def messages(token, method, budget, **parameters):
    cursor = None
    seen_cursors = set()
    while True:
        remaining = (
            budget.maximum - budget.fetched
            if budget.maximum is not None
            else PAGE_SIZE
        )
        if remaining <= 0:
            budget.truncated = True
            return
        params = dict(parameters, limit=min(PAGE_SIZE, remaining))
        if cursor:
            params["cursor"] = cursor
        result = slack_call(token, method, params)
        page = result.get("messages")
        if not isinstance(page, list):
            raise SlackError(f"{method}: response is missing messages")
        if len(page) > params["limit"]:
            raise SlackError(f"{method}: response exceeded the requested message limit")
        budget.fetched += len(page)
        yield from page
        metadata = result.get("response_metadata") or {}
        next_cursor = metadata.get("next_cursor")
        if not next_cursor:
            if result.get("has_more"):
                raise SlackError(f"{method}: more results reported without a cursor")
            return
        if next_cursor in seen_cursors:
            raise SlackError(f"{method}: repeated pagination cursor")
        if budget.maximum is not None and budget.fetched >= budget.maximum:
            budget.truncated = True
            return
        seen_cursors.add(next_cursor)
        cursor = next_cursor


def find_matches(token, channel, user_id, contains, max_messages):
    matches = {}
    budget = MessageBudget(max_messages)
    folded_contains = contains.casefold() if contains is not None else None

    def consider(message, is_reply=False):
        if (
            message.get("type") == "message"
            and message.get("user") == user_id
            and not message.get("bot_id")
            and isinstance(message.get("ts"), str)
            and isinstance(message.get("text"), str)
            and (
                folded_contains is None
                or folded_contains in message["text"].casefold()
            )
        ):
            matches[message["ts"]] = (message, is_reply)

    for message in messages(
        token, "conversations.history", budget, channel=channel
    ):
        consider(message)
        if message.get("reply_count", 0) > 0:
            thread_ts = message.get("thread_ts", message.get("ts"))
            if not thread_ts:
                raise SlackError("Thread with replies is missing its timestamp")
            for reply in messages(
                token,
                "conversations.replies",
                budget,
                channel=channel,
                ts=thread_ts,
            ):
                if reply.get("ts") != thread_ts:
                    consider(reply, is_reply=True)

    return sorted(matches.values(), key=lambda item: not item[1]), budget


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("channel", help="Slack conversation ID, e.g. C... or D...")
    parser.add_argument(
        "--contains", help="Only match messages containing this text (case-insensitive)"
    )
    parser.add_argument(
        "--user",
        help="Target Slack user ID (default: token owner; Slack permissions still apply)",
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        help="Maximum records to fetch (default: 500 without --contains; unlimited with it)",
    )
    parser.add_argument(
        "--delete", action="store_true", help="Prompt to delete; otherwise preview only"
    )
    args = parser.parse_args()
    token = os.environ.get("SLACK_TOKEN", "").strip()
    if not token:
        parser.error("set the SLACK_TOKEN environment variable to a Slack user token")
    if not token.startswith("xoxp-"):
        parser.error("SLACK_TOKEN must be a Slack user token (xoxp-...)")
    if args.contains == "":
        parser.error("--contains cannot be empty; omit it to match all messages")
    if args.max_messages is not None and args.max_messages < 1:
        parser.error("--max-messages must be at least 1")
    max_messages = args.max_messages
    if max_messages is None and args.contains is None:
        max_messages = 500

    identity = slack_call(token, "auth.test")
    user_id = args.user or identity.get("user_id")
    if not user_id:
        raise SlackError("auth.test did not return a user ID; specify --user")
    limit_description = (
        f"up to {max_messages} message records"
        if max_messages is not None
        else "all available message records"
    )
    print(
        f"Scanning {args.channel} for messages by {user_id} ({limit_description}; "
        f"at least {REQUEST_INTERVAL_SECONDS:g}s between API requests)...",
        file=sys.stderr,
    )
    found, budget = find_matches(
        token, args.channel, user_id, args.contains, max_messages
    )
    for message, is_reply in found:
        kind = "reply" if is_reply else "message"
        replies = message.get("reply_count", 0)
        suffix = f" ({replies} replies)" if replies else ""
        print(f"{message['ts']} [{kind}]{suffix} {message['text']!r}")
    print(f"\n{len(found)} matching messages in {args.channel}.")
    if budget.truncated:
        print(
            f"Scan stopped at --max-messages ({max_messages}); results may be incomplete.",
            file=sys.stderr,
        )
        if args.delete:
            print("Deletion skipped because the scan was incomplete.", file=sys.stderr)
            return
    if not found or not args.delete:
        if found:
            print("Preview only. Add --delete to rescan and request confirmation.")
        return
    if any(not is_reply and message.get("reply_count", 0) for message, is_reply in found):
        print("Warning: some matching messages are thread parents with replies.")
    try:
        confirmation = input("Type DELETE to permanently delete these messages: ")
    except EOFError:
        raise SlackError("No confirmation received; nothing deleted") from None
    if confirmation != "DELETE":
        print("Cancelled; nothing deleted.")
        return
    for index, (message, _) in enumerate(found, 1):
        try:
            slack_call(
                token,
                "chat.delete",
                {"channel": args.channel, "ts": message["ts"]},
                write=True,
            )
        except SlackError as error:
            raise SlackError(
                f"Stopped after deleting {index - 1}/{len(found)} messages; "
                f"failed at {message['ts']}: {error}"
            ) from error
        print(f"Deleted {index}/{len(found)}: {message['ts']}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except SlackError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted; some messages may already have been deleted.", file=sys.stderr)
        sys.exit(130)
