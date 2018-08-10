import argparse
import json
import logging
import os
import typing
from contextlib import closing
from functools import partial

import skein
import tensorflow as tf

from ._internal import (
    KVBarrier,
    MonitoredThread,
    iter_available_sock_addrs,
    decode_fn,
    xset_environ
)
from .cluster import ExperimentFn


def dispatch(
    experiment_fn: ExperimentFn,
    task_type: str,
    task_id: int,
    spec: typing.Dict[str, typing.List[str]]
) -> MonitoredThread:
    # Preempt to ensure all tasks in the cluster are ready to
    # accept incoming traffic by the time we create the training
    # session. Note that "evaluator" does not need a cluster,
    # and (for unknown reasons) "ps" does not follow the same
    # code path as the rest and spawns a server regardless of
    # the "environment" value.
    fake_google_env = task_type != "evaluator" and task_type != "ps"
    xset_environ(TF_CONFIG=json.dumps({
        "cluster": spec,
        "task": {"type": task_type, "index": task_id},
        "environment": "google" if fake_google_env else ""
    }))

    experiment = experiment_fn()
    config = experiment.config
    assert config.task_type == task_type
    assert config.task_id == task_id

    if fake_google_env:
        # XXX at this point the socket has already been closed.
        #     Therefore, there is a potential race with concurrent
        #     applications running on the same node. However,
        #     ``tf.train.Server`` provides no API for wrapping an
        #     existing TCP socket.
        tf.train.Server(
            config.cluster_spec,
            job_name=config.task_type,
            task_index=config.task_id,
            config=config.session_config,
            start=True)

    thread = MonitoredThread(
        name=f"{task_type}:{task_id}",
        target=partial(tf.estimator.train_and_evaluate, *experiment),
        # "ps" tasks do not terminate by themselves. See
        # https://github.com/tensorflow/tensorflow/issues/4713
        daemon=task_type == "ps")
    thread.start()

    tf.logging.info(f"Started {task_type}:{task_id}")

    # "ps" tasks never terminate and therefore cannot be joined.
    if task_type != "ps":
        thread.join()

    return thread


def main(
    experiment_fn: ExperimentFn,
    num_workers: int,
    num_ps: int
) -> None:
    task = os.environ["SKEIN_CONTAINER_ID"]
    task_type, task_id = task.split("_", 1)
    task_id = int(task_id)
    client = skein.ApplicationClient.from_current()

    with closing(iter_available_sock_addrs()) as it:
        init_barrier = KVBarrier(client.kv, "init", num_workers, num_ps)
        host, port = next(it)
        spec = init_barrier.wait(task, f"{host}:{port}")

    thread = dispatch(experiment_fn, task_type, task_id, spec)
    stop_barrier = KVBarrier(client.kv, "stop", num_workers, num_ps)
    stop_barrier.wait(task)
    if thread.exception() is not None:
        raise thread.exception()


if __name__ == "__main__":
    logging.basicConfig(level="INFO")

    parser = argparse.ArgumentParser(description="")
    parser.add_argument("--num-workers", type=int)
    parser.add_argument("--num-ps", type=int)
    parser.add_argument("--experiment-fn", type=str)

    try:
        experiment_fn = decode_fn(os.environ["EXPERIMENT_FN"])
    except KeyError:
        parser.error("EXPERIMENT_FN environment variable must be set")
    else:
        args = parser.parse_args()
        main(experiment_fn, args.num_workers, args.num_ps)
