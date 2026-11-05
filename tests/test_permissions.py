"""Unit test of the permission matrix itself (no HTTP, no database)."""

import pytest

from project_dashboard.db.models import ProjectRole
from project_dashboard.services.permissions import Action, is_allowed

OWNER, PARTICIPANT = ProjectRole.OWNER, ProjectRole.PARTICIPANT

# The rules from the brief, written out independently of the implementation.
EXPECTED = {
    Action.VIEW: (True, True),
    Action.UPDATE: (True, True),
    Action.UPLOAD_DOCUMENT: (True, True),
    Action.DELETE: (True, False),
    Action.DELETE_DOCUMENT: (True, False),
    Action.INVITE: (True, False),
}


def test_every_action_has_an_expected_rule() -> None:
    assert set(EXPECTED) == set(Action)


@pytest.mark.parametrize("action", list(Action))
def test_permission_matrix(action: Action) -> None:
    owner_allowed, participant_allowed = EXPECTED[action]

    assert is_allowed(OWNER, action) is owner_allowed
    assert is_allowed(PARTICIPANT, action) is participant_allowed
