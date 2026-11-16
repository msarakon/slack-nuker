# Slack Message Cleanup

A small command-line script to find messages from a Slack user in one conversation. It previews matches by default and can delete them after an explicit confirmation.

## Requirements

- Python 3.8 or later
- A Slack user token (`xoxp-...`) stored in the `SLACK_TOKEN` environment variable
- Token access to the conversation history and permission to delete the selected messages

The token needs `chat:write` and the appropriate conversation history scope for the conversation type, such as `channels:history`, `groups:history`, `im:history`, or `mpim:history`. Slack permissions and workspace policies still apply.

## Create and install a Slack app

1. Open the [Slack API app dashboard](https://api.slack.com/apps) and choose **Create New App** > **From an app manifest**.
2. Select the workspace where you want to use the app, choose YAML, and paste this manifest:

	 ```yaml
	 display_information:
		 name: Message Cleanup

	 oauth_config:
		 scopes:
			 user:
				 - chat:write
				 - channels:history
				 - groups:history
				 - im:history
				 - mpim:history
	 ```

3. Create the app, then install it to the workspace. If Slack asks for approval, a workspace administrator must approve the installation.
4. In the app settings, open **OAuth & Permissions** and copy the **User OAuth Token**. It starts with `xoxp-`; use this value for `SLACK_TOKEN` below. Do not use the Bot User OAuth Token, client secret, or signing secret.
5. Keep the token private. If scopes are changed later, reinstall the app to the workspace so the updated permissions take effect, then use the current User OAuth Token.

The user token represents the user who authorized the app. By default, the script scans for that user; `--user` selects another Slack user ID but does not grant additional access.

## Usage

Replace `C0123456789` in the examples with the ID of the Slack conversation you want to scan. For a channel, open its details in Slack and find the **Channel ID** near the bottom, or copy the ID from its link (the part after `/archives/`). Direct-message conversation IDs commonly start with `D`.

Set the token in your shell without putting it in the command itself:

```sh
export SLACK_TOKEN='xoxp-your-user-token'
```

Preview messages authored by the token owner in a conversation:

```sh
python3 slack_delete_messages.py C0123456789
```

Only show messages containing a case-sensitive substring:

```sh
python3 slack_delete_messages.py C0123456789 --contains 'old announcement'
```

Unfiltered scans fetch at most 500 message records across conversation history and thread replies by default. When `--contains` is specified, the script scans all available history and replies unless you explicitly set `--max-messages`. Results are filtered locally, so this exhaustive search can take time. Slack requests are paced at least 1.2 seconds apart, using pages of up to 200 records.

Set an explicit cap for any scan with `--max-messages`, for example:

```sh
python3 slack_delete_messages.py C0123456789 --max-messages 100
```

To target a different user, provide their Slack user ID:

```sh
python3 slack_delete_messages.py C0123456789 --user U0123456789
```

To delete matching messages, add `--delete`:

```sh
python3 slack_delete_messages.py C0123456789 --contains 'old announcement' --delete
```

The script scans again, lists the matches, and prompts for the exact text `DELETE` before deleting anything. Any other response cancels the operation. Run `python3 slack_delete_messages.py --help` for all options.

## Behavior and cautions

- Without `--delete`, the script does not delete messages.
- It scans conversation history and thread replies, matching regular messages by the specified user. Bot-authored messages are excluded.
- `--contains` is a case-sensitive substring match. Omit it to match all eligible messages.
- If a scan reaches an explicit `--max-messages` limit while more results remain, the preview is marked incomplete and `--delete` will not proceed. `--contains` filters locally and does not reduce the messages fetched from Slack.
- Deletion is permanent. Review the preview before confirming, especially when using `--user` or omitting `--contains`.
- Slack API failures stop the deletion loop, but messages deleted before the failure are not restored. An interruption may also leave a partially completed deletion.
- Scanning many messages or threads can take time. Requests are paced to avoid bursts, and the script also honors Slack's `Retry-After` delay if the API still rate-limits a request.
- The script relies on Slack's API access and permissions; it cannot bypass workspace restrictions.
