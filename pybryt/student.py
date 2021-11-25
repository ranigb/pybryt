"""Student implementations for PyBryt"""

__all__ = ["StudentImplementation", "check", "generate_student_impls"]

import hashlib
import inspect
import nbformat
import os
import warnings

from glob import glob
from multiprocessing import Process, Queue
from types import FrameType
from typing import Any, Dict, List, Optional, Tuple, Union

from .execution import (
    create_collector, execute_notebook, get_tracing_frame, MemoryFootprint, NBFORMAT_VERSION, 
    tracing_off, tracing_on, TRACING_VARNAME)
from .reference import generate_report, ReferenceImplementation, ReferenceResult
from .utils import pickle_and_hash, Serializable


CACHE_DIR_NAME = ".pybryt_cache"
CACHE_STUDENT_IMPL_PREFIX = "student_impl_{}.pkl"


class StudentImplementation(Serializable):
    """
    A student implementation class for handling the execution of student work and manging the 
    memory footprint generated by that execution.

    Args:
        path_or_nb (``str`` or ``nbformat.NotebookNode``): the submission notebook or the path to it
        addl_filenames (``list[str]``, optional): additional filenames to trace inside during 
            execution
        output (``str``, optional): a path at which to write executed notebook
        timeout (``int``, optional): number of seconds to allow for notebook execution; set to 
            ``None`` for no time limit
    """

    nb: Optional[nbformat.NotebookNode]
    """the submission notebook"""

    nb_path: Optional[str]
    """the path to the notebook file"""

    footprint: MemoryFootprint
    """"""

    def __init__(
        self,
        path_or_nb: Optional[Union[str, nbformat.NotebookNode]],
        addl_filenames: List[str] = [],
        output: Optional[str] = None,
        timeout: Optional[int] = 1200,
    ):
        if path_or_nb is None:
            self.nb = None
            self.nb_path = None
            return
        if isinstance(path_or_nb, str):
            self.nb = nbformat.read(path_or_nb, as_version=NBFORMAT_VERSION)
            self.nb_path = path_or_nb
        elif isinstance(path_or_nb, nbformat.NotebookNode):
            self.nb = path_or_nb
            self.nb_path = ""
        else:
            raise TypeError(f"path_or_nb is of unsupported type {type(path_or_nb)}")

        self._execute(timeout, addl_filenames=addl_filenames, output=output)

    def _execute(
        self, 
        timeout: Optional[int], 
        addl_filenames: List[str] = [], 
        output: Optional[str] = None
    ) -> None:
        """
        Executes the notebook ``self.nb``.

        Args:
            timeout (``int``): number of seconds to allow for notebook execution; set to 
                ``None`` for no time limit
            addl_filenames (``list[str]``, optional): additional filenames to trace inside during 
                execution
            output (``str``, optional): a path at which to write executed notebook
        """
        self.footprint = execute_notebook(
            self.nb, 
            self.nb_path, 
            addl_filenames=addl_filenames, 
            timeout=timeout,
        )

        if output:
            with open(output, "w+") as f:
                nbformat.write(self.footprint.executed_notebook, f)

        if self.errors:
            nb_path = self.nb_path
            if not nb_path:
                nb_path = "student notebook"
            warnings.warn(f"Executing {nb_path} produced errors in the notebook")

    @property
    def errors(self) -> List[Dict[str, Union[str, List[str]]]]:
        """
        ``list[dict[str, Union[str, list[str]]]]:``: a list of error outputs from the executed notebook
        if present
        """
        if self.footprint.executed_notebook is None:
            return []

        errors = []
        for cell in self.footprint.executed_notebook['cells']:
            if cell['cell_type'] == "code":
                for out in cell['outputs']:
                    if out['output_type'] == "error":
                        errors.append(out)

        return errors

    @classmethod
    def from_footprint(cls, footprint: MemoryFootprint) -> 'StudentImplementation':
        """
        Create a student implementation object from a memory footprint directly, rather than by
        executing a notebook. Leaves the ``nb`` and ``nb_path`` instance variables of the resulting 
        object empty.

        Args:
            footprint (:py:class:`pybryt.execution.memory_footprint.MemoryFootprint`): the memory 
                footprint
            calls (``list[tuple[str, str]]``): the list of function calls
            steps (``int``): the number of execution steps
        """
        stu = cls(None)
        stu.footprint = footprint
        return stu

    @classmethod
    def combine(cls, impls: List['StudentImplementation']) -> 'StudentImplementation':
        """
        Combine a series of student implementations into a single student implementation.

        Collects the memory footprint of each implementation into a single list and offsets the
        timestamp of each object by the total number of steps for each preceding student
        implementation in the list. 

        Assumes that the student implementations are provided in sorted order. Filters out duplicate
        values present in multiple implementations by keeping the earliest.

        Args:
            impls (``list[StudentImplementation]``): the list of implementations to combine

        Returns:
            ``StudentImplementation``: the combined implementation
        """
        footprint = MemoryFootprint.combine(*[impl.footprint for impl in impls])
        return cls.from_footprint(footprint)

    @classmethod
    def from_cache(cls, cache_dir=CACHE_DIR_NAME, combine=True) -> \
            Union['StudentImplementation', List['StudentImplementation']]:
        """
        Load one or more student implementations from a cache.

        All files are combined into a single student implementation by default, but a list can be
        returned instead by setting ``combine=False``.

        Args:
            cache_dir (``str``, optional): the path to the cache directory
            combine (``bool``, optional): whether to combine the implementations

        Returns:
            ``StudentImplementation`` or ``list[StudentImplementation]``: the student implementations
            loaded from the cache.
        """
        impls = []
        for impl_path in glob(os.path.join(cache_dir, CACHE_STUDENT_IMPL_PREFIX.format("*"))):
            impls.append(cls.load(impl_path))
        if combine:
            return cls.combine(impls)
        else:
            return impls

    def __eq__(self, other: Any) -> bool:
        """
        Checks whether a student implementation is equal to another object.

        The object is considered equal if it is also a student implementation, has the same memory
        footprint, the same number of steps, and the same source notebook.
        """
        return isinstance(other, type(self)) and self.footprint == other.footprint \
            and self.nb == other.nb

    @property
    def _default_dump_dest(self) -> str:
        return "student.pkl"

    def check(self, ref: Union[ReferenceImplementation, List[ReferenceImplementation]], group=None) -> \
            Union[ReferenceResult, List[ReferenceResult]]:
        """
        Checks this student implementation against a single or list of reference implementations.
        Returns the :py:class:`ReferenceResult<pybryt.ReferenceResult>` object(s) resulting from 
        those checks.

        Args:
            ref (``ReferenceImplementation`` or ``list[ReferenceImplementation]``): the reference(s)
                to run against
            group (``str``, optional): if specified, only annotations in this group will be run

        Returns:
            ``ReferenceResult`` or ``list[ReferenceResult]``: the results of the reference 
            implementation checks
        """
        if isinstance(ref, ReferenceImplementation):
            return ref.run(self.values, group=group)
        elif isinstance(ref, list):
            return [r.run(self.values, group=group) for r in ref]
        else:
            raise TypeError(f"check cannot take values of type {type(ref)}")

    def check_plagiarism(self, student_impls: List["StudentImplementation"], **kwargs) -> List[ReferenceResult]:
        """
        Checks this student implementation against a list of other student implementations for 
        plagiarism. Uses :py:meth:`create_references<pybryt.plagiarism.create_references>` to create
        a randomly-generated reference implementation from this student implementation and runs it
        against each of the implementations in ``student_impls`` using 
        :py:meth:`get_impl_results<pybryt.plagiarism.get_impl_results>`.

        Args:
            student_impls (``list[StudentImplementation]``): other student implementations to run
                against
            **kwargs: keyword arguments passed to 
                :py:meth:`create_references<pybryt.plagiarism.create_references>` and 
                :py:meth:`get_impl_results<pybryt.plagiarism.get_impl_results>`
        
        Returns:
            ``list[ReferenceResult]`` or ``numpy.ndarray``: the results of each student 
            implementation in ``student_impls`` when run against this student implementation
        """
        refs = create_references([self], **kwargs)
        return get_impl_results(refs[0], student_impls, **kwargs)


def generate_student_impls(
    paths_or_nbs: List[Union[str, nbformat.NotebookNode]], parallel: bool = False, **kwargs
) -> List[StudentImplementation]:
    """
    Generates multiple student implementations from a list of file paths or notebooks.

    Can optionally generate the student implementations in parallel processes using Python's 
    ``multiprocessing`` library to reduce the runtime.

    Args:
        paths_or_nbs (``list[Union[str, nbformat.NotebookNode]]``): the notebooks or paths to them
        parallel (``bool``, optional): whether to execute in parallel
        **kwargs: additional keyword arguments passed to the 
            :py:class:`StudentImplementation<pybryt.StudentImplementation>` constructor

    Returns:
        ``list[StudentImplementation]``: the student implementations
    """
    if parallel:
        def create_and_collect_impl(impl, queue, **kwargs):
            stu = StudentImplementation(impl, **kwargs)
            queue.put((impl, stu))

    impls = []
    if parallel:
        queue = Queue()

    for stu in paths_or_nbs:
        if parallel:
            p = Process(target=create_and_collect_impl, args=(stu, queue), kwargs=kwargs)
            p.start()
            impls.append(p)
        else:
            impls.append(StudentImplementation(stu, **kwargs))

    if parallel:
        for p in impls:
            p.join()

        procs, impls = impls, {}
        while not queue.empty():
            t = queue.get()
            impls[str(t[0])] = t[1]

        impls = [impls[str(p)] for p in paths_or_nbs]

    return impls


class check:
    """
    A context manager for running a block of student code against a reference implementation.

    This context manager, designed to be used in students' notebooks, can be used to check a block
    of student code against one or a series of reference implementations. The context manager turns
    on tracing before launching the block and captures the memory footprint that is created while
    the block is executed. It prints out a report upon exiting with information about the passing or
    failing of each reference and the messages returned.

    As an example, the block below uses this context manager to test a student's implementation of
    a Fibonacci generator ``fiberator``:

    .. code-block:: python

        with pybryt.check("fiberator.pkl"):
            fib = fiberator()
            for _ in range(100):
                next(fib)

    Args:
        ref (``Union[str, ReferenceImplementation, list[str], list[ReferenceImplementation]]``): the
            reference(s) to check against or the path(s) to them
        report_on_error (``bool``, optional): whether to print the report when an error is thrown
            by the block
        show_only (one of ``{'satisified', 'unsatisfied', None}``, optional): which types of
            reference results to include in the report; if ``None``, all are included
        **kwargs: additional keyword arguments passed to ``pybryt.execution.create_collector``
    """

    _ref: List[ReferenceImplementation]
    """the references being checked against"""

    _report_on_error: bool
    """whether to print the report when an error is thrown by the block"""

    _show_only: Optional[str]
    """which types of eference results to include in the report"""

    _frame: Optional[FrameType]
    """the frame containing the student's code"""

    _footprint: Optional[MemoryFootprint]
    """the memory footprint"""

    _cache: bool
    """whether to cache the memory footprint and results"""

    _kwargs: Dict[str, Any]
    """keyword arguments passed to ``pybryt.execution.create_collector``"""

    def __init__(
        self, ref: Union[str, ReferenceImplementation, List[str], List[ReferenceImplementation]], 
        report_on_error: bool = True, show_only: Optional[str] = None, cache: bool = True, **kwargs
    ):
        if isinstance(ref, str):
            ref = ReferenceImplementation.load(ref)
        if isinstance(ref, ReferenceImplementation):
            ref = [ref]
        if isinstance(ref, list):
            if len(ref) == 0:
                raise ValueError("Cannot check against an empty list of references")
            if not all(isinstance(r, ReferenceImplementation) for r in ref):
                if not all(isinstance(r, str) for r in ref):
                    raise TypeError("Invalid values in the reference list")
                ref = [ReferenceImplementation.load(r) for r in ref]
        if not all(isinstance(r, ReferenceImplementation) for r in ref):  # pragma: no cover
            raise TypeError("Invalid values provided for reference(s)")

        self._ref = ref
        self._kwargs = kwargs
        self._show_only = show_only
        self._frame = None
        self._footprint = None
        self._report_on_error = report_on_error
        self._cache = cache

    def _cache_check(self, stu, res):
        """
        Cache the student implementation and reference results in the PyBryt cache directory.

        Args:
            stu (``StudentImplementation``): the student implementation created by the check
            res (``Union[ReferenceImplementation, list[ReferenceImplementation]]``): the reference 
                results
        """
        os.makedirs(CACHE_DIR_NAME, exist_ok=True)  # create cache directory if needed

        if not isinstance(res, list):
            res = [res]

        for r in res:
            res_path = os.path.join(CACHE_DIR_NAME, f"{r.name}_results.pkl")
            r.dump(res_path)

        ref_hash = hashlib.sha1("".join(r.name for r in res).encode()).hexdigest()
        stu_path = os.path.join(CACHE_DIR_NAME, CACHE_STUDENT_IMPL_PREFIX.format(ref_hash))
        stu.dump(stu_path)

    def __enter__(self):
        if get_tracing_frame() is not None:
            return  # if already tracing, no action required

        else:
            self._footprint, cir = create_collector(**self._kwargs)
            self._frame = inspect.currentframe().f_back
            self._frame.f_globals[TRACING_VARNAME] = True

            tracing_on(tracing_func=cir)

    def __exit__(self, exc_type, exc_value, traceback):
        tracing_off(save_func=False)

        if self._frame is not None:
            self._frame.f_globals[TRACING_VARNAME] = False

            if exc_type is None or self._report_on_error:
                stu = StudentImplementation.from_footprint(self._footprint)
                res = stu.check(self._ref)
                report = generate_report(res, show_only=self._show_only)
                if report:
                    print(report)

                if self._cache:
                    self._cache_check(stu, res)

        return False


from .plagiarism import create_references, get_impl_results
