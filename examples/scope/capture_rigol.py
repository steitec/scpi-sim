"""Read a waveform from a Rigol DS1054Z with the procedure from its programming guide.

Start the simulator, then run this file; against a real DS1054Z only the address changes:

    scpi-sim serve --signal 1:sine,freq=1k,vpp=2 --profile rigol-ds1054z
    python examples/scope/capture_rigol.py

Needs: pip install pyvisa pyvisa-py
"""

import pyvisa

rm = pyvisa.ResourceManager("@py")
scope = rm.open_resource("TCPIP0::127.0.0.1::5025::SOCKET", read_termination="\n", write_termination="\n", timeout=5000)
print(scope.query("*IDN?"))

scope.write(":TIM:SCAL 0.0002")  # 200 us/div: 2.4 periods of 1 kHz on the 12 divisions
scope.write(":CHAN1:SCAL 0.5")
scope.write(":TRIG:EDG:SOUR CHAN1")
scope.write(":TRIG:EDG:LEV 0")
scope.write(":SING")
while scope.query(":TRIG:STAT?") != "STOP":
    pass

scope.write(":WAV:SOUR CHAN1")
scope.write(":WAV:MODE NORM")
scope.write(":WAV:FORM BYTE")
_, _, points, _, xinc, xorigin, _, yinc, yorigin, yref = scope.query(":WAV:PRE?").split(",")
codes = scope.query_binary_values(":WAV:DATA?", datatype="B", container=list)

times = [float(xorigin) + i * float(xinc) for i in range(len(codes))]
volts = [(code - int(yorigin) - int(yref)) * float(yinc) for code in codes]
print(f"{points} points, {times[0] * 1e3:+.2f} ms to {times[-1] * 1e3:+.2f} ms, {min(volts):+.2f} V to {max(volts):+.2f} V")
print("frequency:", scope.query(":MEAS:ITEM? FREQ,CHAN1"), "peak-peak:", scope.query(":MEAS:ITEM? VPP,CHAN1"))
scope.close()
rm.close()
