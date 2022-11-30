import struct
import time
import sys
from pybluemo import YaspBlueGigaClient, YaspClient
from pybluemo import MSG_CLASS_BY_RSP_CODE

from pybluemo import MsgError, MsgAccelStream, MsgRtcSync, MsgConnParamUpdate, MsgSoftReset, MsgAdsAnalogStream
from pybluemo import MsgDataSinkConfig, MsgDataSinkControl
from pybluemo import MsgSpiFlashRead, MsgSpiFlashErase, MsgSpiFlashInit

from pybluemo import EnumModify, EnumDataSink, EnumAccelDataRate, EnumAdsDataRate, EnumAdsPga, EnumAdsInputMux

UUT = b"Bluemo v2.0"
COM = "COM16"
nvm_filename = "nvm_data.dat"
parsed_filename = "parsed_data.csv"


def rtc_to_int(bvalue):
    return sum([bvalue[i] * (1 << (8 * i)) for i in range(len(bvalue))])


# Returns a float value which is the number of seconds since Reset
def get_rtc(yasp_client):
    resp = yasp_client.send_command(callback=None, msg_defn=MsgRtcSync.builder())
    return rtc_to_int(resp.get_param("RtcCounter")) * 0.0000305


def get_flash_info(yasp_client):
    mem_info = yasp_client.send_command(callback=None, msg_defn=MsgDataSinkConfig.builder())
    start = mem_info.get_param("StartAddress")
    flash_info = yasp_client.send_command(callback=None, msg_defn=MsgSpiFlashInit.builder())
    block_size = flash_info.get_param("BlockSizeBytes")
    end = flash_info.get_param("FlashSizeKilobytes") * 1024
    page_size = flash_info.get_param("PageSizeBytes")
    return start, end, block_size, page_size


def initiate_collection(yasp_client):
    rtc_save = MsgDataSinkControl.builder(1, MsgRtcSync.get_command_code(), data_sink=EnumDataSink.SPI_FLASH)
    print(yasp_client.send_command(callback=None, msg_defn=rtc_save))
    ads_save = MsgDataSinkControl.builder(1, MsgAdsAnalogStream.get_command_code(), data_sink=EnumDataSink.SPI_FLASH)
    print(yasp_client.send_command(callback=None, msg_defn=ads_save))
    yasp_client.send_command(callback=lambda rsp: print(rsp), msg_defn=MsgRtcSync.builder())
    yasp_client.send_command(callback=lambda rsp: print(rsp),
                             msg_defn=MsgAdsAnalogStream.builder(instance=0,
                                                                 data_range=EnumAdsPga.FSR0P256,
                                                                 data_rate=EnumAdsDataRate.CUSTOM_PERIOD,
                                                                 custom_period=10, watermark=16))


def download_data(yasp_client, filename=nvm_filename):
    rtc_save = MsgDataSinkControl.builder(1, MsgRtcSync.get_command_code(), data_sink=EnumDataSink.BLE)
    print(yasp_client.send_command(callback=None, msg_defn=rtc_save))
    print("Device Time: %f" % get_rtc(yasp_client))
    yasp_client.send_command(callback=lambda rsp: print(rsp), msg_defn=MsgRtcSync.builder())
    yasp_client.send_command(callback=lambda rsp: print(rsp),
                             msg_defn=MsgAdsAnalogStream.builder(data_rate=EnumAdsDataRate.SINGLE_SAMPLE))
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
            break
            #print(".", end="")
    yasp_client.send_command(callback=lambda rsp: print(rsp), msg_defn=MsgSoftReset.builder())


class DatParser(object):
    def __init__(self, out_filename=parsed_filename):
        self.out_fp = open(out_filename, "w")
        self.running_counter = 0
        self.rtc_value = 0

    def handle_rtc_data(self, resp):
        b = resp.get_param("RtcCounter")
        filler = b"\x00" * (8 - len(b))
        counter = b + filler
        self.rtc_value = struct.unpack("<Q", counter)[0]

    def handle_accel_data(self, resp):
        self.out_fp.write(str(resp))

    def handle_ads_data(self, resp):
        adc_data = resp.get_param("AdcData")
        sample_count = resp.get_param("Watermark")
        values = struct.unpack("<%dh" % sample_count, adc_data)
        for value in values:
            self.out_fp.write("\n%d,%d" % (self.running_counter, value))
            if self.rtc_value > 0:
                self.out_fp.write(",%d" % self.rtc_value)
                self.rtc_value = 0
            self.running_counter += 1
        #self.out_fp.write(str(values))


def parse_data(filename=nvm_filename, out_filename=parsed_filename):
    dat_parser = DatParser(out_filename)
    with open(filename, "rb") as fp:
        parser = YaspClient(MSG_CLASS_BY_RSP_CODE)
        parser.set_default_msg_callback(MsgRtcSync.get_response_code(), lambda resp: dat_parser.handle_rtc_data(resp))
        parser.set_default_msg_callback(MsgAccelStream.get_response_code(), lambda resp: dat_parser.handle_accel_data(resp))
        parser.set_default_msg_callback(MsgAdsAnalogStream.get_response_code(), lambda resp: dat_parser.handle_ads_data(resp))
        progress = 0
        data = fp.read(512)
        while len(data) > 0:
            print("Parsing(%08X)..." % progress)
            progress += len(data)
            parser.serial_rx(data)
            data = fp.read(512)
            #time.sleep(1)


def check_progress(yasp_client):
    print(yasp_client.send_command(callback=None, msg_defn=MsgDataSinkConfig.builder()))


def main():
    print(sys.argv)
    if "--parse" in sys.argv:
        if len(sys.argv) > 2:
            print("Parsing %s..." % sys.argv[2])
            parse_data(sys.argv[2])
        else:
            print("Parsing %s..." % nvm_filename)
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
    elif "--check" in sys.argv:
        check_progress(yasp_client)
    time.sleep(1)
    client.disconnect(conn_handle)


if __name__ == "__main__":
    main()
