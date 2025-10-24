from ib_insync import IB

ib = IB()
ib.connect("127.0.0.1", 7497, clientId=99)

positions = ib.positions()
print("---- OPEN POSITIONS ----")
if not positions:
    print("Inga öppna positioner.")
else:
    for p in positions:
        print(p)

ib.disconnect()
