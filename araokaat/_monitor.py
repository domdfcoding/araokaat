#!/usr/bin/env python3
#
#  _monitor.py
"""

"""
#
#  Copyright © 2026 Dominic Davis-Foster <dominic@davis-foster.co.uk>
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
from threading import Event, Thread, current_thread
from time import time
from typing import TYPE_CHECKING, List, Type
from warnings import warn

if TYPE_CHECKING:
	# this package
	import araokaat

__all__ = ["SynchronisationWarning", "TMonitor"]


class SynchronisationWarning(RuntimeWarning):
	"""
	Warning for multi-thread/-process errors which may cause incorrect nesting but otherwise no adverse effects.
	"""


class TMonitor(Thread):
	"""
	Monitoring thread for progressbars.
	Monitors if progressbars are taking too much time to display
	and readjusts miniters automatically if necessary.

	:param cls: Progressbar class to use.
	:param sleep_interval: Time to sleep between monitoring checks.
	"""

	_test: dict = {}  # internal vars for unit testing

	def __init__(self, cls: Type["araokaat.araokaat"], sleep_interval: float):
		Thread.__init__(self, name="araokaat_monitor")
		self.daemon = True  # kill thread when main killed (KeyboardInterrupt)
		self.woken = 0  # last time woken up, to sync with monitor
		self.araokaat_cls = cls
		self.sleep_interval = sleep_interval
		self._time = self._test.get("time", time)
		self.was_killed = self._test.get("Event", Event)()
		atexit.register(self._atexit_signal)
		self.start()

	def _atexit_signal(self) -> None:
		"""
		Non-joining shutdown signal.

		Avoids deadlocks at interpreter exit from other threads, dead forks, etc.
		This daemon thread is auto-reaped on shutdown without needing a join.
		"""

		self.was_killed.set()

	def exit(self) -> bool:
		self.was_killed.set()

		if self is not current_thread():
			self.join()

		return self.report()

	def get_instances(self) -> List["araokaat.araokaat"]:
		# returns a copy of started `araokaat_cls` instances
		return [
				i for i in self.araokaat_cls._instances.copy()
				# Avoid race by checking that the instance started
				if hasattr(i, "start_t")
				]

	def run(self) -> None:
		cur_t = self._time()
		while True:
			# After processing and before sleeping, notify that we woke
			# Need to be done just before sleeping
			self.woken = cur_t
			# Sleep some time...
			self.was_killed.wait(self.sleep_interval)
			# Quit if killed
			if self.was_killed.is_set():
				return
			# Then monitor!
			# Acquire lock (to access _instances)
			with self.araokaat_cls.get_lock():
				cur_t = self._time()
				# Check instances are waiting too long to print
				instances = self.get_instances()
				for instance in instances:
					# Check event in loop to reduce blocking time on exit
					if self.was_killed.is_set():
						return
					# Only if mininterval > 1 (else iterations are just slow)
					# and last refresh exceeded maxinterval
					if (instance.miniters > 1 and (cur_t - instance.last_print_t) >= instance.maxinterval):
						# force bypassing miniters on next iteration
						# (dynamic_miniters adjusts mininterval automatically)
						instance.miniters = 1
						# Refresh now! (works only for manual tqdm)
						instance.refresh(nolock=True)
					# Remove accidental long-lived strong reference
					del instance
				if instances != self.get_instances():  # pragma: nocover
					warn(
							"Set changed size during iteration" + " (see https://github.com/tqdm/tqdm/issues/481)",
							SynchronisationWarning,
							stacklevel=2,
							)
				# Remove accidental long-lived strong references
				del instances

	def report(self) -> bool:
		return not self.was_killed.is_set()
