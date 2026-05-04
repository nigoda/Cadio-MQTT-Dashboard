"""
Smart Irrigation — State Machine Engine

States:
  IDLE, WAIT_CONDITION, INIT_SET, INIT_VERIFY,
  ACTION_SET, ACTION_VERIFY, ACTION_RUN,
  ACTION_REVERT, ACTION_VERIFY_REVERT, BUFFER,
  PAUSED_CONDITION, PAUSED_SCHEDULE, COMPLETED,
  ERROR_SET, ERROR_VERIFY, ERROR
"""

import time
import logging
from datetime import datetime
from enum import Enum

from config import VERIFY_TIMEOUT_SEC, MAX_RETRIES, DEFAULT_BUFFER_SEC

log = logging.getLogger(__name__)


class State(str, Enum):
    IDLE = "IDLE"
    WAIT_CONDITION = "WAIT_CONDITION"
    INIT_SET = "INIT_SET"
    INIT_VERIFY = "INIT_VERIFY"
    ACTION_SET = "ACTION_SET"
    ACTION_VERIFY = "ACTION_VERIFY"
    ACTION_RUN = "ACTION_RUN"
    ACTION_REVERT = "ACTION_REVERT"
    ACTION_VERIFY_REVERT = "ACTION_VERIFY_REVERT"
    BUFFER = "BUFFER"
    PAUSED_CONDITION = "PAUSED_CONDITION"
    PAUSED_SCHEDULE = "PAUSED_SCHEDULE"
    COMPLETED = "COMPLETED"
    ERROR_SET = "ERROR_SET"
    ERROR_VERIFY = "ERROR_VERIFY"
    ERROR = "ERROR"


class Automation:
    """
    A single automation instance with its own state machine.
    """

    def __init__(self, auto_id, data):
        self.id = auto_id
        self.name = data.get("name", f"Automation {auto_id}")
        self.status = data.get("status", False)  # ON/OFF

        # Configuration
        self.schedule = data.get("schedule", {"days": [], "startTime": "00:00", "endTime": "23:59"})
        self.conditions = data.get("conditions", [])  # [{sensor, operator, value}, ...]
        self.initialization = data.get("initialization", [])  # [{switch, state}]
        self.actions = data.get("actions", [])  # [{switch, state, duration}]
        self.error_state = data.get("error_state", [])  # [{switch, state}]
        self.buffer_time = data.get("buffer_time", DEFAULT_BUFFER_SEC)

        # Runtime
        self.state = State.IDLE
        self.current_action_index = 0
        self.timer_start = 0
        self.remaining_time = 0
        self.retry_count = 0
        self.pause_reason = None
        self.init_index = 0  # track which init items are verified
        self.error_index = 0
        self.last_condition_state = None  # for edge detection (FALSE→TRUE)
        self.logs = []  # [{ts, msg}]

    def to_dict(self):
        """Serialize for API/UI."""
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "schedule": self.schedule,
            "conditions": self.conditions,
            "initialization": self.initialization,
            "actions": self.actions,
            "error_state": self.error_state,
            "buffer_time": self.buffer_time,
            "runtime": {
                "state": self.state.value,
                "current_action_index": self.current_action_index,
                "timer_start": self.timer_start,
                "remaining_time": self.remaining_time,
                "retry_count": self.retry_count,
                "pause_reason": self.pause_reason,
            },
            "logs": self.logs[-50:],  # last 50 entries
        }

    def add_log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        entry = {"ts": ts, "msg": msg}
        self.logs.append(entry)
        if len(self.logs) > 200:
            self.logs = self.logs[-100:]
        log.info(f"[{self.name}] {msg}")

    def reset(self):
        """Reset automation — clear progress, keep status."""
        self.current_action_index = 0
        self.timer_start = 0
        self.remaining_time = 0
        self.retry_count = 0
        self.pause_reason = None
        self.init_index = 0
        self.error_index = 0
        self.last_condition_state = None

        if self.status:
            self.state = State.WAIT_CONDITION
            self.add_log("RESET → WAIT_CONDITION")
        else:
            self.state = State.IDLE
            self.add_log("RESET → IDLE")

    def turn_off(self):
        """Immediate stop."""
        self.status = False
        self.state = State.IDLE
        self.timer_start = 0
        self.remaining_time = 0
        self.retry_count = 0
        self.pause_reason = None
        self.init_index = 0
        self.current_action_index = 0
        self.add_log("STATUS → OFF → IDLE")

    def turn_on(self):
        """Enable automation."""
        self.status = True
        self.state = State.WAIT_CONDITION
        self.current_action_index = 0
        self.timer_start = 0
        self.remaining_time = 0
        self.retry_count = 0
        self.init_index = 0
        self.last_condition_state = None
        self.add_log("STATUS → ON → WAIT_CONDITION")


class Engine:
    """
    Non-blocking execution engine. Call tick() periodically.
    """

    def __init__(self, publish_fn, get_switch_state_fn, get_sensor_state_fn):
        """
        publish_fn(topic, payload): send MQTT command
        get_switch_state_fn(switch_id): returns current actual state ("ON"/"OFF")
        get_sensor_state_fn(sensor_id): returns current sensor value string
        """
        self.publish = publish_fn
        self.get_switch_state = get_switch_state_fn
        self.get_sensor_state = get_sensor_state_fn
        self.automations: dict[str, Automation] = {}

    def add_automation(self, auto_id, data):
        self.automations[auto_id] = Automation(auto_id, data)
        return self.automations[auto_id]

    def remove_automation(self, auto_id):
        if auto_id in self.automations:
            del self.automations[auto_id]

    def get_automation(self, auto_id):
        return self.automations.get(auto_id)

    def tick(self):
        """Called every ENGINE_TICK_INTERVAL. Advances all automations."""
        now = time.time()
        for auto in list(self.automations.values()):
            if not auto.status:
                if auto.state != State.IDLE:
                    auto.state = State.IDLE
                continue
            self._tick_one(auto, now)

    def _tick_one(self, auto: Automation, now: float):
        """Advance one automation's state machine."""
        state = auto.state

        # --- Priority checks (always run) ---
        if state == State.ERROR:
            return  # locked until RESET or OFF

        # Check schedule
        if state not in (State.IDLE, State.ERROR, State.ERROR_SET, State.ERROR_VERIFY, State.COMPLETED):
            if not self._schedule_active(auto):
                if state != State.PAUSED_SCHEDULE:
                    if state == State.ACTION_RUN and auto.timer_start > 0:
                        elapsed = now - auto.timer_start
                        action = auto.actions[auto.current_action_index]
                        auto.remaining_time = action["duration"] - elapsed
                    auto.pause_reason = "schedule"
                    auto.state = State.PAUSED_SCHEDULE
                    auto.add_log("PAUSED (Schedule)")
                return

        # Check condition
        if state not in (State.IDLE, State.ERROR, State.ERROR_SET, State.ERROR_VERIFY, State.WAIT_CONDITION, State.COMPLETED):
            if not self._condition_met(auto):
                if state != State.PAUSED_CONDITION:
                    if state == State.ACTION_RUN and auto.timer_start > 0:
                        elapsed = now - auto.timer_start
                        action = auto.actions[auto.current_action_index]
                        auto.remaining_time = action["duration"] - elapsed
                    auto.pause_reason = "condition"
                    auto.state = State.PAUSED_CONDITION
                    auto.add_log("PAUSED (Condition)")
                return

        # --- State handlers ---
        if state == State.WAIT_CONDITION:
            self._handle_wait_condition(auto)

        elif state == State.PAUSED_CONDITION:
            if self._condition_met(auto):
                self._resume(auto, now)

        elif state == State.PAUSED_SCHEDULE:
            if self._schedule_active(auto):
                self._resume(auto, now)

        elif state == State.INIT_SET:
            self._handle_init_set(auto, now)

        elif state == State.INIT_VERIFY:
            self._handle_init_verify(auto, now)

        elif state == State.ACTION_SET:
            self._handle_action_set(auto, now)

        elif state == State.ACTION_VERIFY:
            self._handle_action_verify(auto, now)

        elif state == State.ACTION_RUN:
            self._handle_action_run(auto, now)

        elif state == State.ACTION_REVERT:
            self._handle_action_revert(auto, now)

        elif state == State.ACTION_VERIFY_REVERT:
            self._handle_action_verify_revert(auto, now)

        elif state == State.BUFFER:
            self._handle_buffer(auto, now)

        elif state == State.COMPLETED:
            self._handle_completed(auto)

        elif state == State.ERROR_SET:
            self._handle_error_set(auto, now)

        elif state == State.ERROR_VERIFY:
            self._handle_error_verify(auto, now)

    # --- State Handlers ---

    def _handle_wait_condition(self, auto: Automation):
        cond = self._condition_met(auto)
        sched = self._schedule_active(auto)

        if cond and sched:
            # Edge detection: only start on FALSE→TRUE transition
            if auto.last_condition_state is False or auto.last_condition_state is None:
                auto.last_condition_state = True
                if auto.initialization:
                    auto.state = State.INIT_SET
                    auto.init_index = 0
                    auto.add_log("Condition+Schedule MET → INIT_SET")
                else:
                    auto.state = State.ACTION_SET
                    auto.current_action_index = 0
                    auto.add_log("Condition+Schedule MET → ACTION_SET")
        else:
            auto.last_condition_state = cond

    def _handle_init_set(self, auto: Automation, now: float):
        # Send all initialization commands in parallel
        for item in auto.initialization:
            self._set_switch(item["switch"], item["state"])
        auto.timer_start = now
        auto.retry_count = 0
        auto.state = State.INIT_VERIFY
        auto.add_log("INIT_SET: commands sent")

    def _handle_init_verify(self, auto: Automation, now: float):
        # Check all init switches match
        all_ok = True
        for item in auto.initialization:
            actual = self.get_switch_state(item["switch"])
            if actual is None or actual.upper() != item["state"].upper():
                all_ok = False
                break

        if all_ok:
            auto.state = State.ACTION_SET
            auto.current_action_index = 0
            auto.add_log("INIT_VERIFY: OK → ACTION_SET")
            return

        # Timeout
        if now - auto.timer_start > VERIFY_TIMEOUT_SEC:
            auto.retry_count += 1
            if auto.retry_count >= MAX_RETRIES:
                auto.add_log("INIT_VERIFY: TIMEOUT → ERROR")
                self._enter_error(auto, now)
            else:
                auto.state = State.INIT_SET
                auto.add_log(f"INIT_VERIFY: retry {auto.retry_count}")

    def _handle_action_set(self, auto: Automation, now: float):
        if auto.current_action_index >= len(auto.actions):
            auto.state = State.COMPLETED
            auto.add_log("All actions done → COMPLETED")
            return

        action = auto.actions[auto.current_action_index]
        self._set_switch(action["switch"], action["state"])
        auto.timer_start = now
        auto.retry_count = 0
        auto.state = State.ACTION_VERIFY
        auto.add_log(f"ACTION_SET [{auto.current_action_index + 1}/{len(auto.actions)}]: {action['switch']} → {action['state']}")

    def _handle_action_verify(self, auto: Automation, now: float):
        action = auto.actions[auto.current_action_index]
        actual = self.get_switch_state(action["switch"])

        if actual is not None and actual.upper() == action["state"].upper():
            # Verified — start timer
            auto.timer_start = now
            duration = action.get("duration", 0)
            if auto.remaining_time > 0:
                # Resuming from pause — use remaining
                duration = auto.remaining_time
                auto.remaining_time = 0
            auto.state = State.ACTION_RUN
            auto.add_log(f"ACTION_VERIFY OK → RUN ({duration}s)")
            return

        if now - auto.timer_start > VERIFY_TIMEOUT_SEC:
            auto.retry_count += 1
            if auto.retry_count >= MAX_RETRIES:
                auto.add_log(f"ACTION_VERIFY TIMEOUT: {action['switch']} → ERROR")
                self._enter_error(auto, now)
            else:
                auto.state = State.ACTION_SET
                auto.add_log(f"ACTION_VERIFY retry {auto.retry_count}")

    def _handle_action_run(self, auto: Automation, now: float):
        action = auto.actions[auto.current_action_index]
        duration = action.get("duration", 0)

        # Use remaining_time if resuming
        effective_duration = duration
        if auto.remaining_time > 0:
            effective_duration = auto.remaining_time
            auto.remaining_time = 0

        elapsed = now - auto.timer_start
        if elapsed >= effective_duration:
            # Duration completed — revert
            auto.state = State.ACTION_REVERT
            auto.add_log(f"ACTION_RUN done → REVERT")
            return

        # State enforcement: ensure switch stays in target state
        actual = self.get_switch_state(action["switch"])
        if actual is not None and actual.upper() != action["state"].upper():
            self._set_switch(action["switch"], action["state"])
            auto.add_log(f"STATE ENFORCE: {action['switch']} forced to {action['state']}")

    def _handle_action_revert(self, auto: Automation, now: float):
        action = auto.actions[auto.current_action_index]
        # Revert = opposite state
        revert_state = "OFF" if action["state"].upper() == "ON" else "ON"
        self._set_switch(action["switch"], revert_state)
        auto.timer_start = now
        auto.retry_count = 0
        auto.state = State.ACTION_VERIFY_REVERT
        auto.add_log(f"ACTION_REVERT: {action['switch']} → {revert_state}")

    def _handle_action_verify_revert(self, auto: Automation, now: float):
        action = auto.actions[auto.current_action_index]
        revert_state = "OFF" if action["state"].upper() == "ON" else "ON"
        actual = self.get_switch_state(action["switch"])

        if actual is not None and actual.upper() == revert_state.upper():
            auto.state = State.BUFFER
            auto.timer_start = now
            auto.add_log("REVERT VERIFIED → BUFFER")
            return

        if now - auto.timer_start > VERIFY_TIMEOUT_SEC:
            auto.retry_count += 1
            if auto.retry_count >= MAX_RETRIES:
                auto.add_log(f"REVERT VERIFY TIMEOUT → ERROR")
                self._enter_error(auto, now)
            else:
                auto.state = State.ACTION_REVERT
                auto.add_log(f"REVERT VERIFY retry {auto.retry_count}")

    def _handle_buffer(self, auto: Automation, now: float):
        if now - auto.timer_start >= auto.buffer_time:
            auto.current_action_index += 1
            auto.remaining_time = 0
            if auto.current_action_index >= len(auto.actions):
                auto.state = State.COMPLETED
                auto.add_log("BUFFER done → COMPLETED")
            else:
                auto.state = State.ACTION_SET
                auto.add_log(f"BUFFER done → next action {auto.current_action_index + 1}")

    def _handle_completed(self, auto: Automation):
        auto.last_condition_state = True  # need FALSE→TRUE to restart
        auto.state = State.WAIT_CONDITION
        auto.current_action_index = 0
        auto.remaining_time = 0
        auto.add_log("COMPLETED → WAIT_CONDITION (needs condition toggle)")

    def _enter_error(self, auto: Automation, now: float):
        if auto.error_state:
            auto.state = State.ERROR_SET
            auto.error_index = 0
            auto.add_log("Entering ERROR_SET")
        else:
            auto.state = State.ERROR
            auto.add_log("ERROR (no error_state defined)")

    def _handle_error_set(self, auto: Automation, now: float):
        for item in auto.error_state:
            self._set_switch(item["switch"], item["state"])
        auto.timer_start = now
        auto.retry_count = 0
        auto.state = State.ERROR_VERIFY
        auto.add_log("ERROR_SET: safe state commands sent")

    def _handle_error_verify(self, auto: Automation, now: float):
        all_ok = True
        for item in auto.error_state:
            actual = self.get_switch_state(item["switch"])
            if actual is None or actual.upper() != item["state"].upper():
                all_ok = False
                break

        if all_ok:
            auto.state = State.ERROR
            auto.add_log("ERROR_VERIFY OK → ERROR (locked)")
            return

        if now - auto.timer_start > VERIFY_TIMEOUT_SEC:
            # Can't even reach safe state — still lock
            auto.state = State.ERROR
            auto.add_log("ERROR_VERIFY TIMEOUT → ERROR (locked, unsafe)")

    # --- Resume from pause ---
    def _resume(self, auto: Automation, now: float):
        prev_state = auto.pause_reason
        auto.pause_reason = None

        # If we have remaining time, resume ACTION_RUN with it
        if auto.remaining_time > 0:
            auto.timer_start = now
            auto.state = State.ACTION_RUN
            auto.add_log(f"RESUMED from {prev_state} → ACTION_RUN ({auto.remaining_time:.0f}s left)")
        elif auto.state in (State.PAUSED_CONDITION, State.PAUSED_SCHEDULE):
            # Resume to where we were — use action_set as safe re-entry
            if auto.current_action_index > 0 or auto.init_index > 0:
                auto.state = State.ACTION_SET
                auto.add_log(f"RESUMED from {prev_state} → ACTION_SET (action {auto.current_action_index + 1})")
            else:
                auto.state = State.INIT_SET
                auto.add_log(f"RESUMED from {prev_state} → INIT_SET")

    # --- Helpers ---

    def _set_switch(self, switch_id, target_state):
        """Publish command to switch."""
        # Command topic format: <state_topic>/set or derived
        # For now, use the switch_id as the command topic base
        payload = f'{{"state": "{target_state.upper()}"}}'
        cmd_topic = switch_id.replace("/state", "/set") if "/state" in switch_id else switch_id + "/set"
        self.publish(cmd_topic, payload)
        log.debug(f"SET: {cmd_topic} = {payload}")

    def _condition_met(self, auto: Automation) -> bool:
        """Evaluate condition expression (inline AND/OR with AND precedence)."""
        if not auto.conditions:
            return True

        # Group by OR, then evaluate AND groups
        # conditions: [{sensor, operator, value, logic}]
        # logic: "AND" | "OR" | None (first item)
        or_groups = []
        current_group = []

        for cond in auto.conditions:
            logic = cond.get("logic", "AND")
            if logic == "OR" and current_group:
                or_groups.append(current_group)
                current_group = []
            current_group.append(cond)

        if current_group:
            or_groups.append(current_group)

        # OR: any group TRUE → overall TRUE
        for group in or_groups:
            # AND: all in group must be TRUE
            group_ok = True
            for cond in group:
                actual = self.get_sensor_state(cond["sensor"])
                expected = cond.get("value", "")
                op = cond.get("operator", "=")

                if actual is None:
                    group_ok = False
                    break

                if not self._compare(actual, op, expected):
                    group_ok = False
                    break

            if group_ok:
                return True

        return False

    def _compare(self, actual: str, op: str, expected: str) -> bool:
        """Compare sensor value."""
        a = actual.strip().upper()
        e = expected.strip().upper()

        if op in ("=", "=="):
            return a == e
        elif op == "!=":
            return a != e
        elif op in (">", "<", ">=", "<="):
            try:
                av = float(actual)
                ev = float(expected)
                if op == ">": return av > ev
                if op == "<": return av < ev
                if op == ">=": return av >= ev
                if op == "<=": return av <= ev
            except ValueError:
                return False
        return a == e

    def _schedule_active(self, auto: Automation) -> bool:
        """Check if current time is within schedule."""
        sched = auto.schedule
        if not sched or not sched.get("days"):
            return True  # no schedule = always active

        now = datetime.now()
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        today = day_names[now.weekday()]

        if today not in sched["days"]:
            return False

        start_str = sched.get("startTime", "00:00")
        end_str = sched.get("endTime", "23:59")

        start_h, start_m = map(int, start_str.split(":"))
        end_h, end_m = map(int, end_str.split(":"))

        start_min = start_h * 60 + start_m
        end_min = end_h * 60 + end_m
        now_min = now.hour * 60 + now.minute

        if start_min <= end_min:
            # Normal range (e.g., 06:00 → 09:00)
            return start_min <= now_min <= end_min
        else:
            # Overnight range (e.g., 22:00 → 06:00)
            return now_min >= start_min or now_min <= end_min
