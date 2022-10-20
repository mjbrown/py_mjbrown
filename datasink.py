import time
import sys
from pybluemo import YaspBlueGigaClient, YaspClient
from pybluemo import MSG_CLASS_BY_RSP_CODE

from pybluemo import MsgError, MsgAccelStream, MsgRtcSync, MsgConnParamUpdate, MsgSoftReset
from pybluemo import MsgDataSinkConfig, MsgDataSinkControl
from pybluemo import MsgSpiFlashRead, MsgSpiFlashErase, MsgSpiFlashInit

from pybluemo import EnumModify, EnumDataSink, EnumAccelDataRate

UUT = b"Bluemo v2.0"
COM = "COM16"
default_filename = "nvm_data.dat"


def rtc_to_int(bvalue):
    return sum([bvalue[i] * (1 << (8 * i)) for i in range(len(bvalue))])


def get_rtc(yasp_client):
    resp = yasp_client.send_command(callback=None, msg_defn=MsgRtcSync.builder())
    return rtc_to_int(resp.get_param("RtcCounter")) * 0.0000305


def get_flash_info(yasp_client):
    mem_info = yasp_client.send_command(callback=None, msg_defn=MsgDataSinkConfig.builder(0))
    start = mem_info.get_param("StartAddress")
    flash_info = yasp_client.send_command(callback=None, msg_defn=MsgSpiFlashInit.builder())
    block_size = flash_info.get_param("BlockSizeBytes")
    end = flash_info.get_param("FlashSizeKilobytes") * 1024
    page_size = flash_info.get_param("PageSizeBytes")
    return start, end, block_size, page_size


def initiate_collection(yasp_client):
    rtc_save = MsgDataSinkControl.builder(1, MsgRtcSync.get_command_code(), data_sink=EnumDataSink.SPI_FLASH)
    print(yasp_client.send_command(callback=None, msg_defn=rtc_save))
    accel_save = MsgDataSinkControl.builder(1, MsgAccelStream.get_command_code(), data_sink=EnumDataSink.SPI_FLASH)
    print(yasp_client.send_command(callback=None, msg_defn=accel_save))
    yasp_client.send_command(callback=lambda rsp: print(rsp), msg_defn=MsgRtcSync.builder())
    yasp_client.send_command(callback=lambda rsp: print(rsp), msg_defn=MsgAccelStream.builder())


def download_data(yasp_client, filename=default_filename):
    yasp_client.send_command(callback=lambda rsp: print(rsp), msg_defn=MsgRtcSync.builder())
    yasp_client.send_command(callback=lambda rsp: print(rsp), msg_defn=MsgAccelStream.builder(data_rate=EnumAccelDataRate.OFF))
    start, end, block_size, page_size = get_flash_info(yasp_client)
    with open(filename, "wb") as fp:
        pages = (end - start) // page_size
        for page in range(pages):
            addr = start + page*page_size
            rd_data = yasp_client.send_command(callback=None, msg_defn=MsgSpiFlashRead.builder(addr, page_size)).get_param("Data")
            if rd_data == b"\xFF"*len(rd_data):
                break
            else:
                print("Address: %08X" % addr)
                fp.write(rd_data)


def erase_all(yasp_client):
    start, end, block_size, page_size = get_flash_info(yasp_client)
    pages = (end - start) // page_size
    for page in range(pages):
        addr = start + page*page_size
        rd = (yasp_client.send_command(callback=None, msg_defn=MsgSpiFlashRead.builder(addr, 16))).get_param("Data")
        if rd != b"\xFF"*len(rd):
            print("Addr: %08X - Data: %s" % (addr, rd))
            print(yasp_client.send_command(callback=None, msg_defn=MsgSpiFlashErase.builder(addr, block_size)))
        else:
            print(".", end="")
    yasp_client.send_command(callback=lambda rsp: print(rsp), msg_defn=MsgSoftReset.builder())


def parse_data(filename=default_filename):
    with open(filename, "rb") as fp:
        def handle_rtc_data(resp):
            print(resp)

        def handle_accel_data(resp):
            print(resp)

        parser = YaspClient(MSG_CLASS_BY_RSP_CODE)
        parser.set_default_msg_callback(MsgRtcSync.get_response_code(), handle_rtc_data)
        parser.set_default_msg_callback(MsgAccelStream.get_response_code(), handle_accel_data)
        progress = 0
        data = fp.read(512)
        while len(data) > 0:
            print("Parsing(%08X)..." % progress)
            progress += len(data)
            parser.serial_rx(data)
            data = fp.read(512)
            time.sleep(1)


def main():
    print(sys.argv)
    if "--parse" in sys.argv:
        if len(sys.argv) > 2:
            print("Parsing %s..." % sys.argv[2])
            parse_data(sys.argv[2])
        else:
            print("Parsing %s..." % default_filename)
            parse_data()
        return
    client = YaspBlueGigaClient(port=COM)
    client.reset_ble_state()
    yasp_client = YaspClient(MSG_CLASS_BY_RSP_CODE)
    conn_handle = client.connect_by_name(UUT, yasp_client)
    time.sleep(1)
    supah_speed = MsgConnParamUpdate.builder(EnumModify.MODIFY, conn_handle=2, conn_interval_max_1p25ms=6)
    print(yasp_client.send_command(callback=None, msg_defn=supah_speed))
    if "--erase" in sys.argv:
        for i in range(3):
            print("Erasing all in %d seconds..." % (3-i))
            time.sleep(1)
        erase_all(yasp_client)
    elif "--initiate" in sys.argv:
        initiate_collection(yasp_client)
    elif "--download" in sys.argv:
        if len(sys.argv) > 3:
            download_data(yasp_client, sys.argv[2])
        else:
            download_data(yasp_client)
    time.sleep(1)
    client.disconnect(conn_handle)


if __name__ == "__main__":
    main()
