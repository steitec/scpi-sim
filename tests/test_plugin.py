"""The pytest plugin, exercised in separate pytest runs (pytester).

The plugin is loaded through its installed entry point, like for a user.
"""

from helpers import BASIC


def test_default_bench(pytester):
    pytester.makepyfile("""
        import socket

        def test_it(scpi_bench):
            s = socket.create_connection(("127.0.0.1", scpi_bench.port("dmm")), timeout=2)
            s.sendall(b"*IDN?\\n")
            assert s.recv(100).startswith(b"scpi-sim,DMM")
            s.close()
            assert scpi_bench.resource("psu").startswith("TCPIP0::127.0.0.1::")
            assert set(scpi_bench.instruments) == {"psu", "dmm", "mux"}
    """)
    pytester.runpytest().assert_outcomes(passed=1)


def test_bench_from_ini_and_fault(pytester):
    pytester.makefile(".yaml", bench=BASIC)
    pytester.makeini("[pytest]\nscpi_bench = bench.yaml\n")
    pytester.makepyfile("""
        def test_it(scpi_bench):
            scpi_bench.mux.process("ROUT:CLOS (@101)")
            scpi_bench.psu.process("VOLT 5;CURR 1;:OUTP ON")
            scpi_bench.dmm.inject("offset", value=1)
            assert scpi_bench.dmm.process("MEAS:VOLT?") == "+6.00000000E+00"
    """)
    pytester.runpytest().assert_outcomes(passed=1)


def test_option_wins_over_ini(pytester):
    pytester.makefile(".yaml", bench=BASIC)
    pytester.makefile(".yaml", other="instruments: {meter: {type: dmm, port: 0}}\n")
    pytester.makeini("[pytest]\nscpi_bench = bench.yaml\n")
    pytester.makepyfile("""
        def test_it(scpi_bench):
            assert set(scpi_bench.instruments) == {"meter"}
    """)
    pytester.runpytest("--scpi-bench", "other.yaml").assert_outcomes(passed=1)


def test_fresh_bench_per_test(pytester):
    pytester.makepyfile("""
        def test_one(scpi_bench):
            scpi_bench.psu.process("VOLT 7")
            scpi_bench.psu.inject("idn", value="X")

        def test_two(scpi_bench):
            assert scpi_bench.psu.process("VOLT?") == "+0.00000000E+00"
            assert scpi_bench.psu.faults == {}
    """)
    pytester.runpytest().assert_outcomes(passed=2)


def test_two_benches_no_clash(pytester):
    pytester.makepyfile("""
        def test_it(scpi_bench, scpi_bench_factory):
            other = scpi_bench_factory({"instruments": {"dmm": {"type": "dmm", "port": 0}}})
            assert other.port("dmm") != scpi_bench.port("dmm")
            other.dmm.volts = 1.0
            assert scpi_bench.dmm.volts == 5.0
    """)
    pytester.runpytest().assert_outcomes(passed=1)


def test_factory_accepts_file(pytester):
    pytester.makefile(".yaml", bench=BASIC)
    pytester.makepyfile("""
        def test_it(scpi_bench_factory):
            b = scpi_bench_factory("bench.yaml")
            assert b.port("mux") > 0
    """)
    pytester.runpytest().assert_outcomes(passed=1)


def test_config_error_fails_setup(pytester):
    pytester.makefile(".yaml", bench="instruments: {x: {type: scope, port: 0}}\n")
    pytester.makepyfile("def test_it(scpi_bench): pass\n")
    result = pytester.runpytest("--scpi-bench", "bench.yaml")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*scope*"])
