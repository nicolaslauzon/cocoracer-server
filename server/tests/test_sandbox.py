"""The Landlock rights a car's ruleset handles, per ABI version: each
right only from the version that introduced it, since a ruleset naming a
right the kernel does not know is refused (EINVAL) and every car DNF'd."""

import pytest

from cocoracer_server.sandbox import handled_rights

V1_FS = (1 << 13) - 1
REFER, TRUNCATE, IOCTL_DEV = 1 << 13, 1 << 14, 1 << 15
TCP = 0b11  # bind and connect
SCOPES = 0b11  # abstract unix sockets and signals


@pytest.mark.parametrize(
    ("abi", "fs", "net", "scoped"),
    [
        (1, V1_FS, 0, 0),
        (2, V1_FS | REFER, 0, 0),
        (3, V1_FS | REFER | TRUNCATE, 0, 0),
        (4, V1_FS | REFER | TRUNCATE, TCP, 0),
        (5, V1_FS | REFER | TRUNCATE | IOCTL_DEV, TCP, 0),
        (6, V1_FS | REFER | TRUNCATE | IOCTL_DEV, TCP, SCOPES),
        # Later versions add no right a car's ruleset uses.
        (7, V1_FS | REFER | TRUNCATE | IOCTL_DEV, TCP, SCOPES),
    ],
)
def test_each_abi_handles_only_the_rights_it_knows(
    abi: int, fs: int, net: int, scoped: int
) -> None:
    assert handled_rights(abi) == (fs, net, scoped)
