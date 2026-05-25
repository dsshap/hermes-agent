from gateway.platforms.slack_extensions import (
    SlackIntegration,
    clear_slack_integrations_for_tests,
    iter_slack_integrations,
    register_slack_integration,
)


def setup_function():
    clear_slack_integrations_for_tests()


def teardown_function():
    clear_slack_integrations_for_tests()


def test_register_slack_integration():
    integration = SlackIntegration(name="example", action_ids=("example.action",))

    register_slack_integration(integration)

    assert iter_slack_integrations() == (integration,)


def test_register_slack_integration_is_idempotent_by_name():
    integration = SlackIntegration(name="example", action_ids=("example.action",))

    register_slack_integration(integration)
    register_slack_integration(integration)

    assert iter_slack_integrations() == (integration,)


def test_duplicate_action_id_is_rejected():
    first = SlackIntegration(name="first", action_ids=("example.action",))
    second = SlackIntegration(name="second", action_ids=("example.action",))

    register_slack_integration(first)
    register_slack_integration(second)

    assert iter_slack_integrations() == (first,)


def test_clear_slack_integrations_for_tests():
    register_slack_integration(SlackIntegration(name="example", action_ids=("example.action",)))

    clear_slack_integrations_for_tests()

    assert iter_slack_integrations() == ()
