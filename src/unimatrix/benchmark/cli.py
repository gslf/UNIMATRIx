"""Compatibility entry point for the benchmark command spelling."""


def cli(argv):
    from ..main import cli as main

    return main(argv)
