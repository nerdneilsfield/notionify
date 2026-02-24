"""Upload lifecycle state machine.

Tracks the state of a file upload through its lifecycle and enforces
valid transitions.  Prevents attaching an upload that has not been
completed or has expired.
"""

from __future__ import annotations

from notionify.errors import NotionifyUploadExpiredError
from notionify.models import UploadState


class UploadStateMachine:
    """Finite state machine for a single file upload.

    Valid transitions::

        PENDING    -> UPLOADING
        UPLOADING  -> UPLOADED | FAILED
        UPLOADED   -> ATTACHED | EXPIRED
        EXPIRED    -> UPLOADING  (retry)
        ATTACHED   -> (terminal)
        FAILED     -> (terminal)

    Parameters
    ----------
    upload_id:
        The UUID of the upload being tracked.
    """

    VALID_TRANSITIONS: dict[UploadState, set[UploadState]] = {
        UploadState.PENDING: {UploadState.UPLOADING},
        UploadState.UPLOADING: {UploadState.UPLOADED, UploadState.FAILED},
        UploadState.UPLOADED: {UploadState.ATTACHED, UploadState.EXPIRED},
        UploadState.EXPIRED: {UploadState.UPLOADING},
        UploadState.ATTACHED: set(),
        UploadState.FAILED: set(),
    }

    def __init__(self, upload_id: str) -> None:
        self.upload_id: str = upload_id
        self.state: UploadState = UploadState.PENDING

    def transition(self, new_state: UploadState) -> None:
        """Attempt to transition to *new_state*.

        Parameters
        ----------
        new_state:
            The desired next state.

        Raises
        ------
        ValueError
            If the transition from the current state to *new_state* is
            not valid.
        NotionifyUploadExpiredError
            If the current state is ``EXPIRED`` and the requested
            transition is not back to ``UPLOADING`` (i.e. a retry).
        """
        allowed = self.VALID_TRANSITIONS.get(self.state, set())

        if new_state not in allowed:
            if self.state == UploadState.EXPIRED and new_state != UploadState.UPLOADING:
                raise NotionifyUploadExpiredError(
                    message=(
                        f"Upload {self.upload_id} has expired and cannot "
                        f"transition to {new_state.value}"
                    ),
                    context={
                        "upload_id": self.upload_id,
                        "current_state": self.state.value,
                        "requested_state": new_state.value,
                    },
                )
            raise ValueError(
                f"Invalid state transition: {self.state.value} -> {new_state.value} "
                f"for upload {self.upload_id}. "
                f"Allowed transitions from {self.state.value}: "
                f"{{{', '.join(s.value for s in allowed)}}}"
            )

        self.state = new_state

    def assert_can_attach(self) -> None:
        """Assert that the upload is in a state where it can be attached.

        The upload must be in the ``UPLOADED`` state to be attached to a
        Notion block.

        Raises
        ------
        NotionifyUploadExpiredError
            If the upload has expired.
        ValueError
            If the upload is in any state other than ``UPLOADED``.
        """
        if self.state == UploadState.EXPIRED:
            raise NotionifyUploadExpiredError(
                message=(
                    f"Upload {self.upload_id} has expired and cannot be attached"
                ),
                context={
                    "upload_id": self.upload_id,
                    "current_state": self.state.value,
                },
            )
        if self.state != UploadState.UPLOADED:
            raise ValueError(
                f"Upload {self.upload_id} cannot be attached in state "
                f"{self.state.value}; must be in {UploadState.UPLOADED.value}"
            )
