# Vendored llama.cpp

`llama.cpp` is a **pinned git submodule** at tag `b10333`
(SHA `08659901c43b51de735740f1cf61bb82fbe0c4e4`, recorded in `llama.cpp.pinned-sha`).

Never track `master`. The mtmd (multimodal) C API moves between releases —
`mtmd_encode` is already deprecated in favour of `mtmd_encode_chunk` at this tag —
so an unpinned bump silently changes the surface our Swift compiles against.

## Building the xcframework

The xcframework is a **build artifact and is not committed**. After a fresh clone:

```bash
git submodule update --init --recursive
SnailsNative/vendor/build-llama-xcframework.sh
```

Takes roughly 10–20 minutes (it builds macOS, iOS, tvOS and visionOS slices).
The wrapper refuses to run if the submodule has drifted off the pinned SHA.

Output: `SnailsNative/vendor/llama.xcframework`.

## Two facts worth knowing before you link it

1. **`llama.framework` is a DYNAMIC library at this tag**, despite what you might
   assume from llama.cpp's static `.a` outputs:

   ```
   $ file ios-arm64/llama.framework/llama
   llama: Mach-O 64-bit dynamically linked shared library arm64
   ```

   It must be linked **Embed & Sign**. "Do Not Embed" builds fine and then
   crashes at launch in dyld.

2. **`mtmd.h` and `mtmd-helper.h` are included** in the framework's umbrella
   headers, so a single `import llama` reaches the multimodal API. `LLAMA_BUILD_MTMD`
   defaults to `ON` in llama.cpp's own `build-xcframework.sh`; if those headers are
   missing from a rebuild, the flag got turned off.

The iOS deployment floor is **16.4**, set by `build-xcframework.sh` itself.
