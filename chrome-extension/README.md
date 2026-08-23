# MY Market Radar Browser Bridge

This unpacked Manifest V3 extension connects the local analyzer to the user's
existing signed-in Google Chrome tabs. It polls only `127.0.0.1:9232`; that
bridge port isn't exposed through FRP or the public web application.

The extension uses Chrome's `debugger` permission so the backend can keep the
existing CDP collection and deep-scroll logic while operating the user's real
Shopee/Lazada session. It doesn't bypass verification pages or alter browser
fingerprints.
