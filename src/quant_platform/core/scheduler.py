from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class AssetSchedule:
    asset_config_id: int
    interval: timedelta
    anchor: datetime
    enabled: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.asset_config_id, bool) or not isinstance(self.asset_config_id, int):
            raise TypeError("asset_config_id must be an integer")
        if self.asset_config_id <= 0:
            raise ValueError("asset_config_id must be positive")
        if not isinstance(self.interval, timedelta) or self.interval <= timedelta(0):
            raise ValueError("interval must be positive")
        if not isinstance(self.anchor, datetime):
            raise TypeError("anchor must be a datetime")
        if self.anchor.tzinfo is None or self.anchor.utcoffset() != timedelta(0):
            raise ValueError("anchor must be UTC-aware")
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a boolean")


ScheduleCallback = Callable[[int, datetime], None]


class Scheduler:
    def __init__(
        self,
        schedules: Iterable[AssetSchedule],
        callback: ScheduleCallback,
    ) -> None:
        self._schedules = self._validated_schedules(schedules)
        self._callback = callback
        self._dispatched: dict[int, datetime] = {}

    @staticmethod
    def _validated_schedules(schedules: Iterable[AssetSchedule]) -> tuple[AssetSchedule, ...]:
        ordered = tuple(sorted(schedules, key=lambda schedule: schedule.asset_config_id))
        asset_ids = [schedule.asset_config_id for schedule in ordered]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("asset_config_id must be unique")
        return ordered

    def replace_schedules(self, schedules: Iterable[AssetSchedule]) -> None:
        replacement = self._validated_schedules(schedules)
        previous = {schedule.asset_config_id: schedule for schedule in self._schedules}
        retained_ids = {
            schedule.asset_config_id
            for schedule in replacement
            if previous.get(schedule.asset_config_id) == schedule
        }
        self._dispatched = {
            asset_id: boundary
            for asset_id, boundary in self._dispatched.items()
            if asset_id in retained_ids
        }
        self._schedules = replacement

    def tick(self, now: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("now must be UTC-aware")
        errors: list[Exception] = []
        for schedule in self._schedules:
            if not schedule.enabled or now < schedule.anchor:
                continue
            elapsed_intervals = (now - schedule.anchor) // schedule.interval
            boundary = schedule.anchor + elapsed_intervals * schedule.interval
            if self._dispatched.get(schedule.asset_config_id) == boundary:
                continue
            try:
                self._callback(schedule.asset_config_id, boundary)
            except Exception as error:
                errors.append(error)
                continue
            self._dispatched[schedule.asset_config_id] = boundary
        if errors:
            raise ExceptionGroup("scheduler callbacks failed", errors)