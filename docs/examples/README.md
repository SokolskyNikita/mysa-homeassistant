# Protocol examples

Cloud request/response examples and runnable Python clients now live in the
standalone [`mysa-client examples`](https://github.com/SokolskyNikita/mysa-client/tree/main/examples).
Its offline checker validates the sanitized exchanges without logging in,
discovering accessories, or sending requests.

This integration still maintains the legacy MQTT message examples below. They
are inert documentation data and do not contain a live acknowledgement.

- [Lite command with array encoding](mqtt/thermostat-command.array.json)
- [Lite command with string encoding](mqtt/thermostat-command.string.json)

See the [MQTT protocol reference](../mqtt.md) for how those messages were
recovered and the [documentation maintenance guide](../maintenance.md) for local
offline checks.
