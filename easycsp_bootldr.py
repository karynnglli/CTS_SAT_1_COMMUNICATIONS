#!/usr/bin/python3

class Bootloader:
    UNLOCK_MAGIC1 = 0x45670123
    UNLOCK_MAGIC2 = 0xcdef89ab
    RELOCK_MAGIC  = 0x00000000

    STATUS_ECC_FAIL_L = 0x8000
    STATUS_ECC_CORR_L = 0x4000
    STATUS_ECC_FAIL_H = 0x2000
    STATUS_ECC_CORR_H = 0x1000
    STATUS_B_OK       = 0x0800
    STATUS_A_OK       = 0x0400
    STATUS_BOOT_B     = 0x0200
    STATUS_EPS_CONN   = 0x0100
    STATUS_BUSY       = 0x0008
    STATUS_UNLOCK     = 0x0004
    STATUS_TIMEOUT    = 0x0002
    STATUS_LOG_FULL   = 0x0001

    class Param_V0:
        FLAG_XTEA_UPLINK   = 0x20
        FLAG_HMAC_UPLINK   = 0x10
        FLAG_CRC_UPLINK    = 0x08
        FLAG_XTEA_DOWNLINK = 0x04
        FLAG_HMAC_DOWNLINK = 0x02
        FLAG_CRC_DOWNLINK  = 0x01

        MAGIC = 0x17bafd7a

        def __init__(self, 
                     flags=0x09, 
                     timeout=1000, 
                     hostname='', 
                     xtea_key_hexstr='', 
                     hmac_key_hexstr=''):
            pass
    
    class Metadata_V0:
        MAGIC_A = 0xd2adade2
        MAGIC_B = 0x56bdc248

        def __init__(self, region, size, crc):
            if region.lower() == 'a':
                self.magic = self.MAGIC_A
            elif region.lower() == 'b':
                self.magic = self.MAGIC_B
            else:
                self.magic = 0
            
            self.version = 0
            self.size = size
            self.crc = crc

    class BootCmd:
        BCMD_CFG = 0xec1c
        PARAM_FLAG_EXTENDED = 0x8000
        PARAM_FLAG_FORCE = 0x4000

        def __init__(self, 
                     image_sel='auto', 
                     image_sel_force:bool=False, 
                     timeout_extended:bool=False):
            if image_sel.lower() == 'auto':
                self._val = 0
            elif image_sel.lower() == 'a':
                self._val = 1
            elif image_sel.lower == 'b':
                self._val = 2
            else:
                raise ValueError('invalid image_sel: ' + image_sel)
            
            if image_sel_force:
                if image_sel.lower() != 'auto':
                    self._val |= self.PARAM_FLAG_FORCE

            if timeout_extended:
                self._val = self.PARAM_FLAG_EXTENDED

        @property
        def val(self):
            return self._val

    @staticmethod
    def parse_status(val:int):
        pass

    @staticmethod
    def parse_retcode(x:int):
        pass

    class Transport:
        def __init__(self):
            pass

        def read(self):
            pass
        
        def write(self):
            pass

        def query(self):
            pass

    def __init__(self, cmd_payload_maxlen=128, ack_payload_maxlen=128):
        pass

    @property
    def status(self):
        pass

    @property
    def logs(self):
        pass

    @property
    def regions(self):
        pass

    @property
    def layout(self):
        pass

    def set_timeout(self):
        pass

    def boot(self, target='A'):
        pass

    def __unlock(self):
        pass

    def __lock(self):
        pass

    def erase(self, region=''):
        pass

    def write_data(self, region:str, offset:int, data:bytes):
        pass

    def read_data(self, region:str, offset:int, len:int):
        pass

    def checksum(self, region:str, offset:int):
        pass

    def set_param(self):
        pass

    def set_metadata(self, region:str):
        pass

    def upload(self, region:str, data:bytes):
        pass

    def upload_intelhex(self, region:str, contents:bytes):
        pass

    def upload_file(self, region:str, file:str):
        pass
