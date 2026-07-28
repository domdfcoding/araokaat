#
#  Based on `tqdm`
#  https://github.com/tqdm/tqdm/
#
#  This Source Code Form is subject to the terms of the
#  Mozilla Public License, v. 2.0.
#  If a copy of the MPL was not distributed with this project,
#  You can obtain one at https://mozilla.org/MPL/2.0/.
#

# stdlib
import atexit
from functools import wraps
from io import StringIO
from threading import Event, RLock, Thread, current_thread
from time import sleep, time
from typing import Any, Callable, Optional, Set, Type

# 3rd party
import pytest
from typing_extensions import Self

# this package
from araokaat import TMonitor, araokaat

# this package
from .test_araokaat import closing


class Time:
	"""Fake time class class providing an offset"""
	offset: float = 0

	@classmethod
	def reset(cls) -> None:
		"""zeroes internal offset"""
		cls.offset = 0

	@classmethod
	def time(cls) -> float:
		"""time.time() + offset"""
		return time() + cls.offset

	@staticmethod
	def sleep(dur: float) -> None:
		"""identical to time.sleep()"""
		sleep(dur)

	@classmethod
	def fake_sleep(cls, dur: float) -> None:
		"""adds `dur` to internal offset"""
		cls.offset += dur
		sleep(0.000001)  # sleep to allow interrupt (instead of pass)


class FakeEvent(Event):
	"""patched `threading.Event` where `wait()` uses `Time.fake_sleep()`"""

	def wait(self, timeout: Optional[float] = None) -> bool:
		"""uses Time.fake_sleep"""
		if timeout is not None:
			Time.fake_sleep(timeout)
		return self.is_set()


def patch_sleep(func: Callable) -> Callable:
	"""Temporarily makes TMonitor use Time.fake_sleep"""

	@wraps(func)
	def inner(*args, **kwargs):  # noqa: MAN002
		"""restores TMonitor on completion regardless of Exceptions"""
		TMonitor._test["time"] = Time.time
		TMonitor._test["Event"] = FakeEvent
		if araokaat.monitor:
			assert not araokaat.monitor.get_instances()
			araokaat.monitor.exit()
			del araokaat.monitor
			araokaat.monitor = None
		try:
			return func(*args, **kwargs)
		finally:
			# Check that class var monitor is deleted if no instance left
			araokaat.monitor_interval = 10
			if araokaat.monitor:
				assert not araokaat.monitor.get_instances()
				araokaat.monitor.exit()
				del araokaat.monitor
				araokaat.monitor = None
			TMonitor._test.pop("Event")
			TMonitor._test.pop("time")

	return inner


def cpu_timify(t: Any, timer: Type[Time] = Time) -> Type[Time]:
	"""Force araokaat to use the specified timer instead of system-wide time"""
	t._time = timer.time
	t._sleep = timer.fake_sleep
	t.start_t = t.last_print_t = t._time()
	return timer


class Fakearaokaat:
	_instances: Set["araokaat"] = set()
	get_lock = araokaat.get_lock


def incr(x: float) -> float:
	return x + 1


def incr_bar(x: int) -> float:
	with closing(StringIO()) as our_file:
		for _ in araokaat(range(x), lock_args=(False, ), file=our_file):
			pass
	return incr(x)


@patch_sleep
def test_monitor_thread():
	"""Test dummy monitoring thread"""
	monitor = TMonitor(Fakearaokaat, 10)  # type: ignore[arg-type]
	# Test if alive, then killed
	assert monitor.report()
	monitor.exit()
	assert not monitor.report()
	assert not monitor.is_alive()
	del monitor


@patch_sleep
def test_monitoring_and_cleanup():
	"""Test for stalled araokaat instance and monitor deletion"""
	# Note: should fix miniters for these tests, else with dynamic_miniters
	# it's too complicated to handle with monitoring update and maxinterval...
	maxinterval = araokaat.monitor_interval
	assert maxinterval == 10
	total = 1000

	with closing(StringIO()) as our_file:
		with araokaat(
				total=total,
				file=our_file,
				miniters=500,
				mininterval=0.1,
				maxinterval=maxinterval,
				) as t:
			cpu_timify(t, Time)
			# Do a lot of iterations in a small timeframe
			# (smaller than monitor interval)
			Time.fake_sleep(maxinterval / 10)  # monitor won't wake up
			t.update(500)
			# check that our fixed miniters is still there
			assert t.miniters <= 500  # TODO: should really be == 500
			# Then do 1 it after monitor interval, so that monitor kicks in
			Time.fake_sleep(maxinterval)
			t.update(1)
			# Wait for the monitor to get out of sleep's loop and update araokaat.
			timeend = Time.time()
			assert t.monitor is not None
			while not (t.monitor.woken >= timeend and t.miniters == 1):
				Time.fake_sleep(1)  # Force awake up if it woken too soon
			assert t.miniters == 1  # check that monitor corrected miniters
			# Note: at this point, there may be a race condition: monitor saved
			# current woken time but Time.sleep() happen just before monitor
			# sleep. To fix that, either sleep here or increase time in a loop
			# to ensure that monitor wakes up at some point.

			# Try again but already at miniters = 1 so nothing will be done
			Time.fake_sleep(maxinterval)
			t.update(2)
			timeend = Time.time()
			assert t.monitor is not None
			while t.monitor.woken < timeend:
				Time.fake_sleep(1)  # Force awake if it woken too soon
			# Wait for the monitor to get out of sleep's loop and update
			# araokaat
			assert t.miniters == 1  # check that monitor corrected miniters


@patch_sleep
def test_monitoring_multi():
	"""Test on multiple bars, one not needing miniters adjustment"""
	# Note: should fix miniters for these tests, else with dynamic_miniters
	# it's too complicated to handle with monitoring update and maxinterval...
	maxinterval = araokaat.monitor_interval
	assert maxinterval == 10
	total = 1000

	with closing(StringIO()) as our_file:
		with araokaat(
				total=total,
				file=our_file,
				miniters=500,
				mininterval=0.1,
				maxinterval=maxinterval,
				) as t1:
			# Set high maxinterval for t2 so monitor does not need to adjust it
			with araokaat(
					total=total,
					file=our_file,
					miniters=500,
					mininterval=0.1,
					maxinterval=1E5,
					) as t2:
				cpu_timify(t1, Time)
				cpu_timify(t2, Time)
				# Do a lot of iterations in a small timeframe
				Time.fake_sleep(maxinterval / 10)
				t1.update(500)
				t2.update(500)
				assert t1.miniters <= 500  # TODO: should really be == 500
				assert t2.miniters == 500
				# Then do 1 it after monitor interval, so that monitor kicks in
				Time.fake_sleep(maxinterval)
				t1.update(1)
				t2.update(1)
				# Wait for the monitor to get out of sleep and update araokaat
				timeend = Time.time()
				assert t1.monitor is not None
				while not (t1.monitor.woken >= timeend and t1.miniters == 1):
					Time.fake_sleep(1)
				assert t1.miniters == 1  # check that monitor corrected miniters
				assert t2.miniters == 500  # check that t2 was not adjusted


def test_imap():
	"""Test multiprocessing.Pool"""
	try:
		# stdlib
		from multiprocessing import Pool
	except ImportError as err:
		pytest.skip(str(err))

	pool = Pool()
	res = list(araokaat(pool.imap(incr, range(100)), disable=True))
	pool.close()
	assert res[-1] == 100


# @patch_lock(thread=True)
# def test_threadpool():
#     """Test concurrent.futures.ThreadPoolExecutor"""
#     ThreadPoolExecutor = importorskip('concurrent.futures').ThreadPoolExecutor

#     with ThreadPoolExecutor(8) as pool:
#         res = list(araokaat(pool.map(incr_bar, range(100)), disable=True))
#     assert sum(res) == sum(range(1, 101))


def test_monitor_atexit_does_not_deadlock_on_stuck_get_lock():
	"""Regression: atexit shutdown must not deadlock on stuck get_lock."""
	# Scenario: another lock holder (in a dead fork) blocks the monitor's
	# `self.araokaat_cls.get_lock()`.
	# The monitor thread's `was_killed.wait()` is insufficient to unblock.
	captured = []
	real_register = atexit.register

	def capture(fn: Callable, *a, **k) -> Callable:
		captured.append((fn, a, k))
		return fn

	monitor_in_acquire = Event()

	class SignallingLock:
		"""
        RLock wrapper signalling on first non-setup-thread `acquire`.
        Used to detect (via `Event.wait`) that the monitor thread reached
        the blocking `acquire` call without sleeping/polling.
        """

		def __init__(self):
			self._inner = RLock()
			self._setup_thread = current_thread()

		def acquire(self, *a, **k) -> bool:
			if current_thread() is not self._setup_thread:
				monitor_in_acquire.set()
			return self._inner.acquire(*a, **k)

		def release(self) -> None:
			self._inner.release()

		def __enter__(self: Self) -> Self:
			self.acquire()
			return self

		def __exit__(self, *exc):
			self.release()

	blocking_lock = SignallingLock()
	blocking_lock.acquire()  # held by setup thread; SignallingLock skips signal
	monitor = None
	try:

		class Stuckaraokaat:
			_instances = set()

			@classmethod
			def get_lock(cls) -> SignallingLock:
				return blocking_lock

		atexit.register = capture  # type: ignore[assignment]
		try:
			# tiny sleep_interval
			monitor = TMonitor(Stuckaraokaat, 0.001)  # type: ignore[arg-type]
		finally:
			atexit.register = real_register

		# Wait deterministically for the monitor thread to reach the
		# blocking `self.araokaat_cls.get_lock().acquire()` call.
		assert monitor_in_acquire.wait(
				timeout=2.0,
				), ("monitor did not reach araokaat_cls.get_lock().acquire() within 2s")
		assert captured, "TMonitor.__init__ should have registered an atexit handler"

		# Invoke the captured handler on a daemon helper thread so the
		# test runner is not blocked if the assertion fails.
		fn, args, kwargs = captured[0]
		helper = Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
		helper.start()
		helper.join(timeout=2.0)
		assert not helper.is_alive(), (
			"atexit handler did not return within 2s — it appears to be "
			"joining a monitor thread blocked on araokaat_cls.get_lock(). "
			"Daemon thread should not be joined from atexit."
		)
	finally:
		# Release the blocking lock so the monitor thread can finish.
		blocking_lock.release()
		if monitor is not None:
			monitor.was_killed.set()
			monitor.join(timeout=2.0)
