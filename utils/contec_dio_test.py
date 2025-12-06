import ctypes
import time

# ---- SETTINGS ----
# Change this to match the device name set in CONTEC Configuration Tool
DEVICE_NAME = "DIO000".encode("ascii")

# Bits you want to test (0,1,2)
TEST_BITS = [0, 1, 2]


def main():
    # Load cdio.dll
    try:
        cdio = ctypes.windll.LoadLibrary("cdio.dll")
    except OSError as e:
        print(f"[ERROR] Failed to load cdio.dll: {e}")
        return

    # Define argument / return types for functions we use
    # long DioInit(const char* DeviceName, short* Id);
    cdio.DioInit.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_short)]
    cdio.DioInit.restype = ctypes.c_long

    # long DioOutBit(short Id, short BitNo, unsigned char Data);
    cdio.DioOutBit.argtypes = [ctypes.c_short, ctypes.c_short, ctypes.c_ubyte]
    cdio.DioOutBit.restype = ctypes.c_long

    # long DioExit(short Id);
    cdio.DioExit.argtypes = [ctypes.c_short]
    cdio.DioExit.restype = ctypes.c_long

    # Optional: error string helper (not strictly required)
    # long DioGetErrorString(long ErrorCode, char* ErrorString);
    try:
        cdio.DioGetErrorString.argtypes = [ctypes.c_long, ctypes.c_char_p]
        cdio.DioGetErrorString.restype = ctypes.c_long
    except AttributeError:
        cdio.DioGetErrorString = None

    def print_error(prefix: str, ret: int):
        if ret == 0:
            return
        msg = f"ret={ret}"
        if cdio.DioGetErrorString is not None:
            buf = ctypes.create_string_buffer(256)
            cdio.DioGetErrorString(ret, buf)
            msg = f"ret={ret}, msg={buf.value.decode(errors='ignore')}"
        print(f"[ERROR] {prefix}: {msg}")

    # ---- Initialize device ----
    dev_id = ctypes.c_short()
    ret = cdio.DioInit(DEVICE_NAME, ctypes.byref(dev_id))
    if ret != 0:
        print_error("DioInit", ret)
        return

    print(f"[INFO] DioInit OK. Device ID = {dev_id.value}")
    print(f"[INFO] Toggling bits {TEST_BITS} ON/OFF every 1 second. Press Ctrl+C to stop.")

    try:
        state = 0
        while True:
            state = 1 - state  # toggle 0 -> 1 -> 0 -> ...

            for bit in TEST_BITS:
                ret = cdio.DioOutBit(dev_id, ctypes.c_short(bit), ctypes.c_ubyte(state))
                if ret != 0:
                    print_error(f"DioOutBit(bit={bit}, state={state})", ret)
                else:
                    print(f"[INFO] Bit {bit} set to {state}")

            time.sleep(1.0)

    except KeyboardInterrupt:
        print("\n[INFO] Ctrl+C received, exiting...")

    finally:
        # Set all test bits back to 0 before exit (optional but nice)
        for bit in TEST_BITS:
            try:
                ret = cdio.DioOutBit(dev_id, ctypes.c_short(bit), ctypes.c_ubyte(0))
                if ret != 0:
                    print_error(f"DioOutBit(bit={bit}, state=0) on exit", ret)
            except Exception:
                pass

        # Properly close the device
        try:
            ret = cdio.DioExit(dev_id)
            if ret != 0:
                print_error("DioExit", ret)
            else:
                print("[INFO] DioExit OK.")
        except Exception as e:
            print(f"[WARN] DioExit raised exception: {e}")


if __name__ == "__main__":
    main()
