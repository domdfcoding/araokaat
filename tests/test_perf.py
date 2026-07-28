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
import sys
from contextlib import contextmanager
from functools import wraps
from time import process_time, sleep, time
from typing import Callable, Iterable, Iterator, List, Optional, TextIO

# 3rd party
import pytest

# this package
from araokaat import araokaat

pytestmark = pytest.mark.slow


def cpu_sleep(t: float) -> None:
	"""Sleep the given amount of cpu time"""
	start = process_time()
	while (process_time() - start) < t:
		pass


def checkCpuTime(sleeptime: float = 0.2) -> bool:
	"""Check if cpu time works correctly"""
	if checkCpuTime.passed:  # type: ignore[attr-defined]
		return True
	# First test that sleeping does not consume cputime
	start1 = process_time()
	sleep(sleeptime)
	t1 = process_time() - start1

	# secondly check by comparing to cpusleep (where we actually do something)
	start2 = process_time()
	cpu_sleep(sleeptime)
	t2 = process_time() - start2

	if abs(t1) < 0.0001 and t1 < t2 / 10:
		checkCpuTime.passed = True  # type: ignore[attr-defined]
		return True
	pytest.skip("cpu time not reliable on this machine")

	return False


checkCpuTime.passed = False  # type: ignore[attr-defined]


@contextmanager
def relative_timer() -> Iterator[Callable[[], float]]:
	"""yields a context timer function which stops ticking on exit"""
	start = process_time()

	def elapser() -> float:
		return process_time() - start

	yield lambda: elapser()
	spent = elapser()

	def elapser() -> float:  # type: ignore[no-redef]
		return spent


def retry_on_except(n: int = 3, check_cpu_time: bool = True) -> Callable[[Callable], Callable[..., None]]:
	"""decroator for retrying `n` times before raising Exceptions"""

	def wrapper(func: Callable) -> Callable[..., None]:
		"""actual decorator"""

		@wraps(func)
		def test_inner(*args, **kwargs) -> None:
			"""may skip if `check_cpu_time` fails"""
			for i in range(1, n + 1):
				try:
					if check_cpu_time:
						checkCpuTime()
					func(*args, **kwargs)
				except Exception:
					if i >= n:
						raise
				else:
					return

		return test_inner

	return wrapper


def simple_progress(
		iterable: Optional[Iterable] = None,
		total: Optional[int] = None,
		file: TextIO = sys.stdout,
		desc: str = '',
		leave: bool = False,
		miniters: float = 1,
		mininterval: float = 0.1,
		width: int = 60,
		) -> Callable[[int], None]:
	"""Simple progress bar reproducing araokaat's major features"""
	n = [0]  # use a closure
	start_t = [time()]
	last_n = [0]
	last_t: List[float] = [0]
	if iterable is not None:
		total = len(iterable)  # type: ignore[arg-type]

	assert total is not None

	def format_interval(t: float) -> str:
		mins, s = divmod(int(t), 60)
		h, m = divmod(mins, 60)
		return f'{h:d}:{m:02d}:{s:02d}' if h else f'{m:02d}:{s:02d}'

	def update_and_print(i: int = 1) -> None:
		n[0] += i
		if (n[0] - last_n[0]) >= miniters:
			last_n[0] = n[0]

			if (time() - last_t[0]) >= mininterval:
				last_t[0] = time()  # last_t[0] == current time

				spent = last_t[0] - start_t[0]
				spent_fmt = format_interval(spent)
				rate = n[0] / spent if spent > 0 else 0
				rate_fmt = "%.2fs/it" % (1.0 / rate) if 0.0 < rate < 1.0 else "%.2fit/s" % rate

				frac = n[0] / total
				percentage = int(frac * 100)
				eta = (total - n[0]) / rate if rate > 0 else 0
				eta_fmt = format_interval(eta)

				# full_bar = "#" * int(frac * width)
				barfill = ' ' * int((1.0 - frac) * width)
				bar_length, frac_bar_length = divmod(int(frac * width * 10), 10)
				full_bar = '#' * bar_length
				frac_bar = chr(48 + frac_bar_length) if frac_bar_length else ' '

				file.write(
						"\r%s %i%%|%s%s%s| %i/%i [%s<%s, %s]" % (
								desc,
								percentage,
								full_bar,
								frac_bar,
								barfill,
								n[0],
								total,
								spent_fmt,
								eta_fmt,
								rate_fmt,
								),
						)

				if n[0] == total and leave:
					file.write('\n')
				file.flush()

	def update_and_yield() -> Iterator:
		assert iterable is not None
		for elt in iterable:
			yield elt
			update_and_print()

	update_and_print(0)
	if iterable is not None:
		return update_and_yield()  # type: ignore[return-value]  # TODO
	return update_and_print


def assert_performance(
		thresh: float,
		name_left: str,
		time_left: float,
		name_right: str,
		time_right: float,
		) -> None:
	"""raises if time_left > thresh * time_right"""
	if time_left > thresh * time_right:
		raise ValueError(
				f'{name_left}: {time_left:f}, {name_right}: {time_right:f}'
				f', ratio {time_left / time_right:f} > {thresh:f}',
				)


@retry_on_except()
def test_manual_basic_overhead():
	"""Test overhead of manual araokaat"""
	total = int(1e6)

	with araokaat(total=total * 10, leave=True) as t:
		a = 0
		with relative_timer() as time_araokaat:
			for i in range(total):
				a += i
				t.update(10)

	a = 0
	with relative_timer() as time_bench:
		for i in range(total):
			a += i
			sys.stdout.write(str(a))

	assert_performance(5, "araokaat", time_araokaat(), "range", time_bench())


def worker(total: int, blocking: bool = True) -> Callable[[int], int]:

	def incr_bar(x: int) -> int:
		for _ in araokaat(
				range(total),
				lock_args=None if blocking else (False, ),
				miniters=1,
				mininterval=0,
				maxinterval=0,
				):
			pass
		return x + 1

	return incr_bar


# @retry_on_except()
# @patch_lock(thread=True)
# def test_lock_args():
#     """Test overhead of nonblocking threads"""
#     ThreadPoolExecutor = pytest.importorskip('concurrent.futures').ThreadPoolExecutor

#     total = 16
#     subtotal = 10000

#     with ThreadPoolExecutor() as pool:
#         sys.stderr.write('block ... ')
#         sys.stderr.flush()
#         with relative_timer() as time_araokaat:
#             res = list(pool.map(worker(subtotal, True), range(total)))
#             assert sum(res) == sum(range(total)) + total
#         sys.stderr.write('noblock ... ')
#         sys.stderr.flush()
#         with relative_timer() as time_noblock:
#             res = list(pool.map(worker(subtotal, False), range(total)))
#             assert sum(res) == sum(range(total)) + total

#     assert_performance(0.5, 'noblock', time_noblock(), 'araokaat', time_araokaat())


@retry_on_except(10)
def test_manual_overhead_hard():
	"""Test overhead of manual araokaat (hard)"""
	total = int(1e5)

	with araokaat(
			total=total * 10,
			leave=True,
			miniters=1,
			mininterval=0,
			maxinterval=0,
			) as t:
		a = 0
		with relative_timer() as time_araokaat:
			for i in range(total):
				a += i
				t.update(10)

	a = 0
	with relative_timer() as time_bench:
		for i in range(total):
			a += i
			sys.stdout.write(("%i" % a) * 40)

	assert_performance(130, "araokaat", time_araokaat(), "range", time_bench())


@retry_on_except(10)
def test_iter_overhead_simplebar_hard():
	"""Test overhead of iteration based araokaat vs simple progress bar (hard)"""
	total = int(1e4)

	a = 0
	with araokaat(
			range(total),
			leave=True,
			miniters=1,
			mininterval=0,
			maxinterval=0,
			) as t:
		with relative_timer() as time_araokaat:
			for i in t:
				a += i
	assert a == (total**2 - total) / 2.0

	a = 0
	s = simple_progress(
			range(total),
			leave=True,
			miniters=1,
			mininterval=0,
			)
	with relative_timer() as time_bench:
		for i in s:  # type: ignore[attr-defined]  # TODO
			a += i

	assert_performance(16, "araokaat", time_araokaat(), "simple_progress", time_bench())


@retry_on_except(10)
def test_manual_overhead_simplebar_hard():
	"""Test overhead of manual araokaat vs simple progress bar (hard)"""
	total = int(1e4)

	with araokaat(
			total=total * 10,
			leave=True,
			miniters=1,
			mininterval=0,
			maxinterval=0,
			) as t:
		a = 0
		with relative_timer() as time_araokaat:
			for i in range(total):
				a += i
				t.update(10)

	simplebar_update = simple_progress(
			total=total * 10,
			leave=True,
			miniters=1,
			mininterval=0,
			)
	a = 0
	with relative_timer() as time_bench:
		for i in range(total):
			a += i
			simplebar_update(10)

	assert_performance(20, "araokaat", time_araokaat(), "simple_progress", time_bench())
