""""""

import re
import linecache
import inspect

from copy import copy
from types import FrameType, FunctionType, ModuleType
from typing import Any, List, Tuple, Callable

from ..utils import make_secret, pickle_and_hash


_COLLECTOR_RET = None
TRACING_VARNAME = "__PYBRYT_TRACING__"
TRACING_FUNC = None


def create_collector(skip_types: List[type] = [type, type(len), ModuleType, FunctionType], addl_filenames: List[str] = []) -> \
        Tuple[List[Tuple[Any, int]], Callable[[FrameType, str, Any], Callable]]:
    """
    Creates a list to collect observed values and a trace function.

    Any types in ``skip_types`` won't be tracked by the trace function. The trace function by 
    default only traces inside IPython but can be set to trace inside specific files using the
    ``addl_filenames`` argument, which should be a list absolute paths to files that should also be
    traced inside of.

    Args:
        skip_types (``list[type]``, optional): object types not to track
        addl_filenames (``list[str]``, optional): filenames to trace inside of in addition to 
            IPython
        
    Returns:
        ``tuple[list[tuple[object, int]], callable[[frame, str, object], callable]]``: the list
        of tuples of observed objects and their timestamps, and the trace function
    """
    global _COLLECTOR_RET
    observed = []
    vars_not_found = {}
    hashes = set()
    counter = [0]

    def track_value(val, seen_at=None):
        """
        Tracks a value in ``observed``. Checks that the value has not already been tracked by 
        pickling it and hashing the pickled object and comparing it to ``hashes``. If pickling is
        unsuccessful, the value is not tracked.

        Args:
            val (``object``): the object to be tracked
            seen_at (``int``, optional): an overriding step counter value
        """
        try:
            if type(val) in skip_types:
                return

            h = pickle_and_hash(val)

            if seen_at is None:
                seen_at = counter[0]
            
            if h not in hashes:
                observed.append((copy(val), seen_at))
                hashes.add(h)

        # if something fails, don't track
        except:
            return

    # TODO: a way to track the cell of execution
    def collect_intermidiate_results(frame: FrameType, event: str, arg: Any):
        """
        Trace function for PyBryt.
        """
        if frame.f_code.co_filename.startswith("<ipython") or frame.f_code.co_filename in addl_filenames:
            counter[0] += 1 # increment student code step counter

        # return if tracking is disabled by a compelxity check
        from .complexity import _TRACKING_DISABLED
        if _TRACKING_DISABLED:
            return collect_intermidiate_results

        name = frame.f_code.co_filename + frame.f_code.co_name

        if frame.f_code.co_filename.startswith("<ipython") or frame.f_code.co_filename in addl_filenames:
            if event == "line" or event == "return":

                line = linecache.getline(frame.f_code.co_filename, frame.f_lineno)
                tokens = set("".join(char if char.isalnum() or char == '_' else "\n" for char in line).split("\n"))
                for t in "".join(char if char.isalnum() or char == '_' or char == '.' else "\n" for char in line).split("\n"):
                    tokens.add(t)
                tokens = sorted(tokens) # sort for stable ordering
                
                for t in tokens:
                    if "." in t:
                        try:
                            val = eval(t, frame.f_globals, frame.f_locals)
                            track_value(val)
                        except:
                            pass

                    else:
                        if t in frame.f_locals:
                            val = frame.f_locals[t]
                            track_value(val)
                                
                        elif t in frame.f_globals:
                            val = frame.f_globals[t]
                            track_value(val)
                
                # for tracking the results of an assignment statement
                m = re.match(r"\s*(\w+)\s=.*", line)
                if m:
                    if name not in vars_not_found:
                        vars_not_found[name] = []
                    vars_not_found[name].append((m.group(1), counter[0]))

            if event == "return":
                track_value(arg)

        elif (frame.f_back.f_code.co_filename.startswith("<ipython") or \
                frame.f_back.f_code.co_filename in addl_filenames) and event == "return":
            track_value(arg)

        if event == "return" and name in vars_not_found:
            varnames = vars_not_found.pop(name)
            for t, step in varnames:
                if t in frame.f_locals:
                    val = frame.f_locals[t]
                    track_value(val, step)

                elif t in frame.f_globals:
                    val = frame.f_globals[t]
                    track_value(val, step)

        return collect_intermidiate_results

    _COLLECTOR_RET = (observed, counter, collect_intermidiate_results)
    return observed, collect_intermidiate_results


def _get_tracing_frame():
    """
    """
    frame = inspect.currentframe()
    while frame is not None:
        if TRACING_VARNAME in frame.f_globals and frame.f_globals[TRACING_VARNAME]:
            return frame
        frame = frame.f_back
    return None


def tracing_off():
    """
    Turns off PyBryt's tracing if tracing is occurring in this call stack. If PyBryt is not tracing,
    takes no action.

    This method can be used in students' notebooks to include code that shouldn't be traced as part
    of the submission, e.g. demo code or ungraded code. In the example below, the call that creates
    ``x2`` is traced but the one to create ``x3`` is not.

    .. code-block:: python

        def pow(x, a):
            return x ** a

        x2 = pow(x, 2)

        pybryt.tracing_off()
        x3 = pow(x, 3)
    """
    global TRACING_FUNC
    frame = _get_tracing_frame()
    if frame is None:
        return
    TRACING_FUNC = frame.f_trace
    vn = f"sys_{make_secret()}"
    exec(f"import sys as {vn}\n{vn}.settrace(None)", frame.f_globals, frame.f_locals)


def tracing_on():
    """
    Turns tracing on if PyBryt was tracing the call stack. If PyBryt is not tracing or
    :py:meth:`tracing_off<pybryt.tracing_off>` has not been called, no action is taken.

    This method can be used in students' notebooks to turn tracing back on after deactivating tracing
    for ungraded code In the example below, ``x4`` is traced because ``tracing_on`` is used after
    ``tracing_off`` and the creation of ``x3``.

    .. code-block:: python

        def pow(x, a):
            return x ** a

        x2 = pow(x, 2)

        pybryt.tracing_off()
        x3 = pow(x, 3)
        pybryt.tracing_on()

        x4 = pow(x, 4)
    """
    global TRACING_FUNC
    frame = _get_tracing_frame()
    if frame is None:
        return
    vn = f"cir_{make_secret()}"
    vn2 = f"sys_{make_secret()}"
    frame.f_globals[vn] = TRACING_FUNC
    exec(f"import sys as {vn2}\n{vn2}.settrace({vn})", frame.f_globals, frame.f_locals)