#================================================================
#================================================================
# CONTEC WDM DIO
# General-purpose Input/Output Sample with I/O Direction Setting
#                                    CONTEC Co.,Ltd.
#                                    Ver1.30(Web Release 2024.08)
#================================================================
#================================================================

import ctypes
import ctypes.wintypes
import sys
import cdio

#================================================================
# Command Define
#================================================================
COMMAND_ERROR = 0           # Error
COMMAND_SET_8255_MODE = 1   # Set 8255 Mode
COMMAND_GET_8255_MODE = 2   # Get 8255 Mode
COMMAND_SET_DM_DIR = 3      # Set input/output direction of device
COMMAND_GET_DM_DIR = 4      # Get input/output direction of device
COMMAND_SET_IO_DIR = 5      # Set I/O direction of I/O ports
COMMAND_GET_IO_DIR = 6      # Get I/O direction of I/O ports
COMMAND_INP_PORT = 7        # 1 Port Input
COMMAND_INP_BIT = 8         # 1 Bit Input
COMMAND_OUT_PORT = 9        # 1 Port Output
COMMAND_OUT_BIT = 10        # 1 Bit Output
COMMAND_ECHO_PORT = 11      # 1 Port Echo Back
COMMAND_ECHO_BIT = 12       # 1 Bit Echo Back
COMMAND_QUIT = 13           # End


#================================================================
# Function that checks if a string can be converted to a number
#================================================================
def isnum(str, base):
    try:
        if 16 == base:
            int(str, 16)
        else:
            int(str)
    except:
        return False
    return True


#================================================================
# Main Function
#================================================================
def main():
    dio_id = ctypes.c_short()
    chip_no = ctypes.c_short()
    ctrl_word = ctypes.c_short()
    io_dir = ctypes.c_ulong()
    dm_dir = ctypes.c_short()
    io_data = ctypes.c_ubyte()
    port_no = ctypes.c_short()
    bit_no = ctypes.c_short()
    err_str = ctypes.create_string_buffer(256)

    #----------------------------------------
    # Initialization
    #----------------------------------------
    dev_name = input('Input device name:')
    lret = cdio.DioInit(dev_name.encode(), ctypes.byref(dio_id))
    if lret != cdio.DIO_ERR_SUCCESS:
        cdio.DioGetErrorString(lret, err_str)
        print(f"DioInit={lret}:{err_str.value.decode('sjis')}")
        sys.exit()
    #----------------------------------------
    # Loop that Wait Input
    #----------------------------------------
    while True:
        #----------------------------------------
        # Display Command
        #----------------------------------------
        print('')
        print('--------------------')
        print(' Menu')
        print('--------------------')
        print('s8 : 8255 mode set')
        print('g8 : 8255 mode get')
        print('sd : DM   direction set')
        print('gd : DM   direction get')
        print('si : I/O  direction set')
        print('gi : I/O  direction get')
        print('ip : port input')
        print('ib : bit  input')
        print('op : port output')
        print('ob : bit  output')
        print('ep : port echoback')
        print('eb : bit  echoback')
        print('q  : quit')
        print('--------------------')
        buf = input('input command:')
        #----------------------------------------
        # Distinguish Command
        #----------------------------------------
        command = COMMAND_ERROR
        #----------------------------------------
        # Set 8255 Mode
        #----------------------------------------
        if buf == 's8':
            command = COMMAND_SET_8255_MODE
        #----------------------------------------
        # Get 8255 Mode
        #----------------------------------------
        if buf == 'g8':
            command = COMMAND_GET_8255_MODE
        #----------------------------------------
        # Set input/output direction of device
        #----------------------------------------
        if buf == 'sd':
            command = COMMAND_SET_DM_DIR
        #----------------------------------------
        # Get input/output direction of device
        #----------------------------------------
        if buf == 'gd':
            command = COMMAND_GET_DM_DIR
        #----------------------------------------
        # Set I/O direction of I/O ports
        #----------------------------------------
        if buf == 'si':
            command = COMMAND_SET_IO_DIR
        #----------------------------------------
        # Get I/O direction of I/O ports
        #----------------------------------------
        if buf == 'gi':
            command = COMMAND_GET_IO_DIR
        #----------------------------------------
        # 1 Port Input
        #----------------------------------------
        if buf == 'ip':
            command = COMMAND_INP_PORT
        #----------------------------------------
        # 1 Bit Input
        #----------------------------------------
        if buf == 'ib':
            command = COMMAND_INP_BIT
        #----------------------------------------
        # 1 Port Output
        #----------------------------------------
        if buf == 'op':
            command = COMMAND_OUT_PORT
        #----------------------------------------
        # 1 Bit Output
        #----------------------------------------
        if buf == 'ob':
            command = COMMAND_OUT_BIT
        #----------------------------------------
        # 1 Port Echo Back
        #----------------------------------------
        if buf == 'ep':
            command = COMMAND_ECHO_PORT
        #----------------------------------------
        # 1 Bit Echo Back
        #----------------------------------------
        if buf == 'eb':
            command = COMMAND_ECHO_BIT
        #----------------------------------------
        # End
        #----------------------------------------
        if buf == 'q':
            command = COMMAND_QUIT
        #----------------------------------------
        # Input Chip Number, Port Number and Bit Number
        #----------------------------------------
        if(command == COMMAND_SET_8255_MODE or
           command == COMMAND_GET_8255_MODE):
           while True:
                buf = input('input chip number:')
                if False == isnum(buf, 10):
                   continue
                chip_no = ctypes.c_short(int(buf))
                break
        elif(command == COMMAND_INP_PORT or
           command == COMMAND_OUT_PORT or
           command == COMMAND_ECHO_PORT):
           while True:
                buf = input('input port number:')
                if False == isnum(buf, 10):
                   continue
                port_no = ctypes.c_short(int(buf))
                break
        elif(command == COMMAND_INP_BIT or
             command == COMMAND_OUT_BIT or
             command == COMMAND_ECHO_BIT):
             while True:
                buf = input('input bit number:')
                if False == isnum(buf, 10):
                   continue
                bit_no = ctypes.c_short(int(buf))
                break
        #----------------------------------------
        # Set the I/O Direction, Input the Output Data
        #----------------------------------------
        if(command == COMMAND_SET_8255_MODE):
            while True:
                buf = input('input control word (Hex):')
                if False == isnum(buf, 16):
                   continue
                ctrl_word = ctypes.c_short(int(buf, 16))
                break
        elif(command == COMMAND_SET_DM_DIR):
            while True:
                buf = input('input DM direction:')
                if False == isnum(buf, 10):
                   continue
                dm_dir = ctypes.c_short(int(buf, 10))
                break
        elif(command == COMMAND_SET_IO_DIR):
            while True:
                buf = input('input I/O direction:')
                if False == isnum(buf, 10):
                   continue
                io_dir = ctypes.c_ulong(int(buf, 10))
                break
        elif(command == COMMAND_OUT_PORT or
            command == COMMAND_OUT_BIT):
            while True:
                buf = input('input data (hex):')
                if False == isnum(buf, 16):
                   continue
                io_data = ctypes.c_ubyte(int(buf, 16))
                break
        #----------------------------------------
        # Execute Command and Display Result
        #----------------------------------------
        #----------------------------------------
        # Set 8255 Mode
        #----------------------------------------
        if command == COMMAND_SET_8255_MODE:
            lret = cdio.DioSet8255Mode(dio_id, chip_no, ctrl_word)
            if lret == cdio.DIO_ERR_SUCCESS:
                print(f'chip number={chip_no.value}, control word (Hex)={ctrl_word.value:02X}')
            else:
                cdio.DioGetErrorString(lret, err_str)
                print(f"DioSet8255Mode={lret}:{err_str.value.decode('sjis')}")
        #----------------------------------------
        # Get 8255 Mode
        #----------------------------------------
        elif command == COMMAND_GET_8255_MODE:
            lret = cdio.DioGet8255Mode(dio_id, chip_no, ctypes.byref(ctrl_word))
            if lret == cdio.DIO_ERR_SUCCESS:
                print(f'chip number={chip_no.value}, control word (Hex)={ctrl_word.value:02X}')
            else:
                cdio.DioGetErrorString(lret, err_str)
                print(f"DioGet8255Mode={lret}:{err_str.value.decode('sjis')}")
        #----------------------------------------
        # Set input/output direction of device
        #----------------------------------------
        elif command == COMMAND_SET_DM_DIR:
            lret = cdio.DioDmSetDirection(dio_id, dm_dir)
            if lret == cdio.DIO_ERR_SUCCESS:
                print(f'DM direction={dm_dir.value}')
            else:
                cdio.DioGetErrorString(lret, err_str)
                print(f"DioDmSetDirection={lret}:{err_str.value.decode('sjis')}")
        #----------------------------------------
        # Get input/output direction of device
        #----------------------------------------
        elif command == COMMAND_GET_DM_DIR:
            lret = cdio.DioDmGetDirection(dio_id, ctypes.byref(dm_dir))
            if lret == cdio.DIO_ERR_SUCCESS:
                print(f'DM direction={dm_dir.value}')
            else:
                cdio.DioGetErrorString(lret, err_str)
                print(f"DioDmGetDirection={lret}:{err_str.value.decode('sjis')}")
        #----------------------------------------
        # Set I/O direction of I/O ports
        #----------------------------------------
        elif command == COMMAND_SET_IO_DIR:
            lret = cdio.DioSetIoDirection(dio_id, io_dir)
            if lret == cdio.DIO_ERR_SUCCESS:
                print(f'I/O direction={io_dir.value}')
            else:
                cdio.DioGetErrorString(lret, err_str)
                print(f"DioSetIoDirection={lret}:{err_str.value.decode('sjis')}")
        #----------------------------------------
        # Get I/O direction of I/O ports
        #----------------------------------------
        elif command == COMMAND_GET_IO_DIR:
            lret = cdio.DioGetIoDirection(dio_id, ctypes.byref(io_dir))
            if lret == cdio.DIO_ERR_SUCCESS:
                print(f'I/O direction={io_dir.value}')
            else:
                cdio.DioGetErrorString(lret, err_str)
                print(f"DioGetIoDirection={lret}:{err_str.value.decode('sjis')}")
        #----------------------------------------
        # 1 Port Input
        #----------------------------------------
        elif command == COMMAND_INP_PORT:
            lret = cdio.DioInpByte(dio_id, port_no, ctypes.byref(io_data))
            if lret == cdio.DIO_ERR_SUCCESS:
                print(f'port number={port_no.value}, data (hex)={io_data.value:02X}')
            else:
                cdio.DioGetErrorString(lret, err_str)
                print(f"DioInpByte={lret}:{err_str.value.decode('sjis')}")
        #----------------------------------------
        # 1 Bit Input
        #----------------------------------------
        elif command == COMMAND_INP_BIT:
            lret = cdio.DioInpBit(dio_id, bit_no, ctypes.byref(io_data))
            if lret == cdio.DIO_ERR_SUCCESS:
                print(f'bit number={bit_no.value}, data (hex)={io_data.value:02X}')
            else:
                cdio.DioGetErrorString(lret, err_str)
                print(f"DioInpBit={lret}:{err_str.value.decode('sjis')}")
        #----------------------------------------
        # 1 Port Output
        #----------------------------------------
        elif command == COMMAND_OUT_PORT:
            lret = cdio.DioOutByte(dio_id, port_no, io_data)
            if lret == cdio.DIO_ERR_SUCCESS:
                print(f'port number={port_no.value}, data (hex)={io_data.value:02X}')
            else:
                cdio.DioGetErrorString(lret, err_str)
                print(f"DioOutByte={lret}:{err_str.value.decode('sjis')}")
        #----------------------------------------
        # 1 Bit Output
        #----------------------------------------
        elif command == COMMAND_OUT_BIT:
            lret = cdio.DioOutBit(dio_id, bit_no, io_data)
            if lret == cdio.DIO_ERR_SUCCESS:
                print(f'bit number={bit_no.value}, data (hex)={io_data.value:02X}')
            else:
                cdio.DioGetErrorString(lret, err_str)
                print(f"DioOutBit={lret}:{err_str.value.decode('sjis')}")
        #----------------------------------------
        # 1 Port Echo Back
        #----------------------------------------
        elif command == COMMAND_ECHO_PORT:
            lret = cdio.DioEchoBackByte(dio_id, port_no, ctypes.byref(io_data))
            if lret == cdio.DIO_ERR_SUCCESS:
                print(f'port number={port_no.value}, data (hex)={io_data.value:02X}')
            else:
                cdio.DioGetErrorString(lret, err_str)
                print(f"DioEchoBackByte={lret}:{err_str.value.decode('sjis')}")
        #----------------------------------------
        # 1 Bit Echo Back
        #----------------------------------------
        elif command == COMMAND_ECHO_BIT:
            lret = cdio.DioEchoBackBit(dio_id, bit_no, ctypes.byref(io_data))
            if lret == cdio.DIO_ERR_SUCCESS:
                print(f'bit number={bit_no.value}, data (hex)={io_data.value:02X}')
            else:
                cdio.DioGetErrorString(lret, err_str)
                print(f"DioEchoBackBit={lret}:{err_str.value.decode('sjis')}")
        #----------------------------------------
        # End
        #----------------------------------------
        elif command == COMMAND_QUIT:
            print(f'quit.')
            print('')
            break
        #----------------------------------------
        # Error
        #----------------------------------------
        elif command == COMMAND_ERROR:
            print(f'error:{buf}')
    #----------------------------------------
    # Exit
    #----------------------------------------
    lret = cdio.DioExit(dio_id)
    if lret != cdio.DIO_ERR_SUCCESS:
        cdio.DioGetErrorString(lret, err_str)
        print(f"DioExit={lret}:{err_str.value.decode('sjis')}")
    #----------------------------------------
    # Terminate application
    #----------------------------------------
    sys.exit()


#----------------------------------------
# Call main function
#----------------------------------------
if __name__ == "__main__":
    main()
