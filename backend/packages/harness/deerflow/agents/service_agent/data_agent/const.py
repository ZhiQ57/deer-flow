DATA_QUERY_VERSION = 1

_STAGE_RANK = {
    "idle": 0,
    "retrieving": 1,
    "needs_refinement": 1,
    "labels_published": 2,
    "awaiting_confirmation": 2,
    "approved": 3,
    "sql_generating": 4,
    "sql_validating": 5,
    "sql_ready": 6,
    "executing": 7,
    "succeeded": 8,
    "failed": 8,
    "cancelled": 8,
}

_TERMINAL_STAGES = {
    "succeeded",
    "failed",
    "cancelled",
    "unsupported_version",
}

_NEW_TURN_STAGES = {
    "idle",
    "retrieving",
    "needs_refinement",
    "labels_published",
}

_RESUMABLE_STAGES = {
    "retrieving",
    "labels_published",
    "awaiting_confirmation",
    "approved",
    "sql_ready",
    "succeeded",
    "failed",
    "cancelled",
}

_NEW_SNAPSHOT_SOURCES = {
    "retrieving": {
        "idle",
        "retrieving",
        "needs_refinement",
        "labels_published",
        "awaiting_confirmation",
    },
    "needs_refinement": {
        "idle",
        "retrieving",
        "needs_refinement",
        "labels_published",
        "awaiting_confirmation",
    },
    "labels_published": {
        "retrieving",
        "labels_published",
    },
    "awaiting_confirmation": {
        "retrieving",
        "labels_published",
    },
    "approved": {
        "retrieving",
        "labels_published",
    },
    "cancelled": {
        "retrieving",
        "labels_published",
    },
}