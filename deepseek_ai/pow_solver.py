"""DeepSeek POW Solver - Direct Python WASM implementation"""

import json
import base64
import struct
import os

# Try different WASM runtimes
try:
    import wasmtime
    HAS_WASMTIME = True
except ImportError:
    HAS_WASMTIME = False

try:
    import wasm3
    HAS_WASM3 = True
except ImportError:
    HAS_WASM3 = False


class DeepSeekHashWasmtime:
    """DeepSeek Hash using wasmtime - Exact port from Node.js"""
    
    def __init__(self, wasm_path: str):
        self.wasm_path = wasm_path
        self.engine = wasmtime.Engine()
        self.store = wasmtime.Store(self.engine)
        
        with open(wasm_path, 'rb') as f:
            wasm_bytes = f.read()
        
        module = wasmtime.Module(self.engine, wasm_bytes)
        self.instance = wasmtime.Instance(self.store, module, [])
        
        # Get exports
        self.memory = self.instance.exports(self.store)['memory']
        self.add_to_stack_pointer = self.instance.exports(self.store)['__wbindgen_add_to_stack_pointer']
        self.export_0 = self.instance.exports(self.store)['__wbindgen_export_0']
        self.export_1 = self.instance.exports(self.store)['__wbindgen_export_1']
        self.wasm_solve = self.instance.exports(self.store)['wasm_solve']
        
        self.offset = 0
    
    def _get_memory_view(self):
        """Get memory as bytes-like object"""
        data_ptr = self.memory.data_ptr(self.store)
        data_len = self.memory.data_len(self.store)
        import ctypes
        ptr_value = ctypes.cast(data_ptr, ctypes.c_void_p).value
        return (ctypes.c_uint8 * data_len).from_address(ptr_value)
    
    def _encode_string(self, text: str, allocate, reallocate=None):
        """Encode string to WASM memory - EXACT port from Node.js"""
        text_bytes = text.encode('utf-8')
        
        if reallocate is None:
            # Simple path
            ptr = allocate(self.store, len(text_bytes), 1)
            ptr = int(ptr) & 0xFFFFFFFF
            
            memory = self._get_memory_view()
            for i, byte in enumerate(text_bytes):
                memory[ptr + i] = byte
            
            self.offset = len(text_bytes)
            return ptr
        
        # Complex path with reallocate
        str_length = len(text)
        ptr = allocate(self.store, str_length, 1)
        ptr = int(ptr) & 0xFFFFFFFF
        
        memory = self._get_memory_view()
        ascii_length = 0
        
        # Write ASCII characters
        for i in range(str_length):
            char_code = ord(text[i])
            if char_code > 127:
                break
            memory[ptr + i] = char_code
            ascii_length += 1
        
        if ascii_length != str_length:
            # Handle non-ASCII
            if ascii_length > 0:
                text = text[ascii_length:]
            
            text_bytes = text.encode('utf-8')
            new_size = ascii_length + len(text_bytes)
            ptr = reallocate(self.store, ptr, str_length, new_size, 1)
            ptr = int(ptr) & 0xFFFFFFFF
            
            # Re-get memory view after realloc
            memory = self._get_memory_view()
            
            # Write remaining bytes
            for i, byte in enumerate(text_bytes):
                memory[ptr + ascii_length + i] = byte
            
            ascii_length += len(text_bytes)
        
        self.offset = ascii_length
        return ptr
    
    def calculate_hash(self, algorithm: str, challenge: str, salt: str, difficulty: int, expire_at: int):
        """Calculate hash answer"""
        if algorithm != 'DeepSeekHashV1':
            raise ValueError(f'Unsupported algorithm: {algorithm}')
        
        prefix = f"{salt}_{expire_at}_"
        
        try:
            # Allocate stack space
            retptr = self.add_to_stack_pointer(self.store, -16)
            retptr = int(retptr) & 0xFFFFFFFF
            
            # Encode challenge
            ptr0 = self._encode_string(
                challenge,
                self.export_0,
                self.export_1
            )
            len0 = self.offset
            
            # Encode prefix
            ptr1 = self._encode_string(
                prefix,
                self.export_0,
                self.export_1
            )
            len1 = self.offset
            
            # Call wasm_solve
            self.wasm_solve(self.store, retptr, ptr0, len0, ptr1, len1, float(difficulty))
            
            # Read result
            memory = self._get_memory_view()
            
            # Read status (Int32, little-endian)
            status = int.from_bytes(
                bytes(memory[retptr + i] for i in range(4)),
                byteorder='little',
                signed=True
            )
            
            # Read value (Float64, little-endian)
            value_bytes = bytes(memory[retptr + 8 + i] for i in range(8))
            value = struct.unpack('<d', value_bytes)[0]
            
            if status == 0:
                return None
            
            return int(value)
            
        finally:
            self.add_to_stack_pointer(self.store, 16)


class DeepSeekHashWasm3:
    """DeepSeek Hash using wasm3"""
    
    def __init__(self, wasm_path: str):
        import wasm3
        
        self.env = wasm3.Environment()
        self.rt = self.env.new_runtime(1024 * 1024)  # 1MB stack
        
        with open(wasm_path, 'rb') as f:
            wasm_bytes = f.read()
        
        self.mod = self.env.parse_module(wasm_bytes)
        self.rt.load(self.mod)
        
        # Get exports
        self.memory = self.rt.get_memory(0)
        self.add_to_stack_pointer = self.rt.find_function("__wbindgen_add_to_stack_pointer")
        self.export_0 = self.rt.find_function("__wbindgen_export_0")
        self.export_1 = self.rt.find_function("__wbindgen_export_1")
        self.wasm_solve = self.rt.find_function("wasm_solve")
        
        self.offset = 0
    
    def _encode_string(self, text: str, allocate, reallocate=None):
        """Encode string to WASM memory"""
        text_bytes = text.encode('utf-8')
        
        if reallocate is None:
            ptr = allocate(len(text_bytes), 1)
            for i, byte in enumerate(text_bytes):
                self.memory[ptr + i] = byte
            self.offset = len(text_bytes)
            return ptr
        
        str_length = len(text)
        ptr = allocate(str_length, 1)
        
        ascii_length = 0
        for i in range(str_length):
            char_code = ord(text[i])
            if char_code > 127:
                break
            self.memory[ptr + i] = char_code
            ascii_length += 1
        
        if ascii_length != str_length:
            if ascii_length > 0:
                text = text[ascii_length:]
            
            text_bytes = text.encode('utf-8')
            new_size = ascii_length + len(text_bytes)
            ptr = reallocate(ptr, str_length, new_size, 1)
            
            for i, byte in enumerate(text_bytes):
                self.memory[ptr + ascii_length + i] = byte
            
            ascii_length += len(text_bytes)
        
        self.offset = ascii_length
        return ptr
    
    def calculate_hash(self, algorithm: str, challenge: str, salt: str, difficulty: int, expire_at: int):
        """Calculate hash answer"""
        if algorithm != 'DeepSeekHashV1':
            raise ValueError(f'Unsupported algorithm: {algorithm}')
        
        prefix = f"{salt}_{expire_at}_"
        
        # Allocate stack space at end of memory
        retptr = len(self.memory) - 16
        
        # Encode strings
        ptr0 = self._encode_string(challenge, self.export_0, self.export_1)
        len0 = self.offset
        
        ptr1 = self._encode_string(prefix, self.export_0, self.export_1)
        len1 = self.offset
        
        # Call wasm_solve
        self.wasm_solve(retptr, ptr0, len0, ptr1, len1, float(difficulty))
        
        # Read result
        status = int.from_bytes(
            bytes(self.memory[retptr + i] for i in range(4)),
            byteorder='little',
            signed=True
        )
        
        value_bytes = bytes(self.memory[retptr + 8 + i] for i in range(8))
        value = struct.unpack('<d', value_bytes)[0]
        
        if status == 0:
            return None
        
        return int(value)


# Global instance
_hash_instance = None
_wasm_path = None


def _find_wasm_file():
    """Find WASM file"""
    global _wasm_path
    if _wasm_path is not None:
        return _wasm_path
    
    possible_paths = [
        os.path.join(os.path.dirname(__file__), 'sha3_wasm_bg.7b9ca65ddd.wasm'),
        os.path.join(os.path.dirname(__file__), '..', 'sha3_wasm_bg.7b9ca65ddd.wasm'),
        r'E:eepseek-reverse-apixampleshat2APIha3_wasm_bg.7b9ca65ddd.wasm',
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            _wasm_path = path
            return path
    
    raise FileNotFoundError('WASM file not found')


def get_deepseek_hash():
    """Get DeepSeekHash singleton instance"""
    global _hash_instance
    if _hash_instance is None:
        wasm_path = _find_wasm_file()
        
        # Try wasmtime first, then wasm3
        if HAS_WASMTIME:
            try:
                _hash_instance = DeepSeekHashWasmtime(wasm_path)
                return _hash_instance
            except Exception as e:
                print(f'wasmtime failed: {e}')
        
        if HAS_WASM3:
            try:
                _hash_instance = DeepSeekHashWasm3(wasm_path)
                return _hash_instance
            except Exception as e:
                print(f'wasm3 failed: {e}')
        
        raise RuntimeError('No WASM runtime available')
    
    return _hash_instance


def calculate_challenge_answer(challenge: dict) -> str:
    """Calculate challenge answer and return base64 encoded string"""
    algorithm = challenge.get('algorithm')
    challenge_str = challenge.get('challenge')
    salt = challenge.get('salt')
    difficulty = challenge.get('difficulty')
    expire_at = challenge.get('expire_at')
    signature = challenge.get('signature')
    
    hash_calculator = get_deepseek_hash()
    answer = hash_calculator.calculate_hash(
        algorithm, challenge_str, salt, difficulty, expire_at
    )
    
    if answer is None:
        raise ValueError('Challenge calculation failed - WASM returned no answer')
    
    challenge_answer = {
        'algorithm': algorithm,
        'challenge': challenge_str,
        'salt': salt,
        'answer': answer,
        'signature': signature,
        'target_path': '/api/v0/chat/completion',
    }
    
    return base64.b64encode(json.dumps(challenge_answer, separators=(',', ':')).encode()).decode()
