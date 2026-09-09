class ProjectPlanNotFoundError(Exception):
    pass


class ProjectPlanInvalidError(Exception):
    def __init__(self, message: str, *, schedule_failure: dict | None = None):
        super().__init__(message)
        self.schedule_failure = schedule_failure
