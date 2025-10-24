from ib_insync import IB

ib = IB()
print("[try connect] 127.0.0.1:7497 cid=123")
ib.connect("127.0.0.1", 7497, clientId=123, timeout=10)
print("[connected?]", ib.isConnected())
ib.disconnect()
print("[done]")
