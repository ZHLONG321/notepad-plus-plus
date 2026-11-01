// Capture one HWND through the compositor, including QPC frame timestamps.
// API contracts: Microsoft Learn, Direct3D11CaptureFrame.SystemRelativeTime,
// IGraphicsCaptureItemInterop::CreateForWindow, CreateFreeThreaded.
#include <windows.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <windows.graphics.capture.interop.h>
#include <windows.graphics.directx.direct3d11.interop.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Graphics.Capture.h>
#include <winrt/Windows.Graphics.DirectX.Direct3D11.h>
#include <deque>
#include <memory>
#include <mutex>
#include <vector>
#include <cstring>
#include <atomic>

using namespace winrt;
using namespace winrt::Windows::Graphics;
using namespace winrt::Windows::Graphics::Capture;
using namespace winrt::Windows::Graphics::DirectX;
using namespace winrt::Windows::Graphics::DirectX::Direct3D11;
struct Frame
{
    int width, height;
    int64_t compositor, received;
    std::vector<uint8_t> data;
    com_ptr<ID3D11Texture2D> texture;
};
struct WindowCapture
{
    std::mutex mutex;
    std::deque<Frame> frames;
    std::atomic<bool> active{true};
    std::atomic<int> error{0};
    int64_t lastFrame = 0, minimumInterval = 0;
    bool deferred = false;
    int gpuCopies = 0, cpuReadbacks = 0;
    com_ptr<ID3D11Device> device;
    com_ptr<ID3D11DeviceContext> context;
    com_ptr<ID3D11Texture2D> staging;
    Direct3D11CaptureFramePool pool{nullptr};
    GraphicsCaptureSession session{nullptr};
    event_token token;
};
static std::shared_ptr<WindowCapture> current;
static com_ptr<ID3D11Device> preparedDevice;
static com_ptr<ID3D11DeviceContext> preparedContext;
static IDirect3DDevice preparedCaptureDevice{nullptr};
static constexpr int recordedFramesPerSecond = 10;
extern "C" __declspec(dllexport) int capture_prepare()
{
    try
    {
        if (preparedCaptureDevice) return 0;
        init_apartment(apartment_type::multi_threaded);
        check_hresult(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0, D3D11_SDK_VERSION,
            preparedDevice.put(), nullptr, preparedContext.put()));
        auto dxgi = preparedDevice.as<IDXGIDevice>();
        check_hresult(dxgi->SetGPUThreadPriority(-7));
        com_ptr<IInspectable> inspectable;
        check_hresult(CreateDirect3D11DeviceFromDXGIDevice(dxgi.get(), inspectable.put()));
        preparedCaptureDevice = inspectable.as<IDirect3DDevice>();
        get_activation_factory<GraphicsCaptureItem, IGraphicsCaptureItemInterop>();
        return 0;
    }
    catch (hresult_error const& error) { return error.code().value; }
}
static int start_capture(uint64_t hwnd, bool deferred)
{
    try
    {
        check_hresult(capture_prepare());
        auto state = std::make_shared<WindowCapture>();
        state->deferred = deferred;
        LARGE_INTEGER frequency;
        QueryPerformanceFrequency(&frequency);
        state->minimumInterval = frequency.QuadPart/recordedFramesPerSecond;
        state->device = preparedDevice;
        state->context = preparedContext;
        auto interop = get_activation_factory<GraphicsCaptureItem, IGraphicsCaptureItemInterop>();
        GraphicsCaptureItem item{nullptr};
        check_hresult(interop->CreateForWindow(reinterpret_cast<HWND>(hwnd),
            guid_of<GraphicsCaptureItem>(), put_abi(item)));
        state->pool = Direct3D11CaptureFramePool::CreateFreeThreaded(
            preparedCaptureDevice, DirectXPixelFormat::B8G8R8A8UIntNormalized, 3, item.Size());
        state->token = state->pool.FrameArrived([state](auto const& pool, auto const&)
        {
            try
            {
                if (!state->active) return;
                auto source = pool.TryGetNextFrame();
                if (!source) return;
                std::lock_guard<std::mutex> lock(state->mutex);
                if (!state->active) return;
                // The startup probe needs only its first five sampled frames.
                // Do not retain capture-pool surfaces or grow GPU storage while
                // the target finishes loading its plugins.
                if (state->deferred && state->frames.size() >= 5) return;
                LARGE_INTEGER now;
                QueryPerformanceCounter(&now);
                if (now.QuadPart-state->lastFrame < state->minimumInterval) return;
                state->lastFrame = now.QuadPart;
                const auto size = source.ContentSize();
                auto surface = source.Surface().as<::Windows::Graphics::DirectX::Direct3D11::IDirect3DDxgiInterfaceAccess>();
                com_ptr<ID3D11Texture2D> texture;
                check_hresult(surface->GetInterface(__uuidof(ID3D11Texture2D), texture.put_void()));
                D3D11_TEXTURE2D_DESC desc;
                texture->GetDesc(&desc);
                Frame frame{size.Width, size.Height, source.SystemRelativeTime().count(), now.QuadPart, {}, {}};
                if (state->deferred)
                {
                    desc.Usage = D3D11_USAGE_DEFAULT;
                    desc.BindFlags = desc.MiscFlags = desc.CPUAccessFlags = 0;
                    check_hresult(state->device->CreateTexture2D(&desc, nullptr, frame.texture.put()));
                    state->context->CopyResource(frame.texture.get(), texture.get());
                    ++state->gpuCopies;
                    state->frames.push_back(std::move(frame));
                    return;
                }
                if (!state->staging)
                {
                    desc.Usage = D3D11_USAGE_STAGING;
                    desc.BindFlags = desc.MiscFlags = 0;
                    desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
                    check_hresult(state->device->CreateTexture2D(&desc, nullptr, state->staging.put()));
                }
                state->context->CopyResource(state->staging.get(), texture.get());
                D3D11_MAPPED_SUBRESOURCE mapped;
                check_hresult(state->context->Map(state->staging.get(), 0, D3D11_MAP_READ, 0, &mapped));
                frame.data.resize(size_t(size.Width)*size.Height*4);
                for (int y=0; y<size.Height; ++y)
                    memcpy(frame.data.data()+size_t(y)*size.Width*4,
                        static_cast<uint8_t*>(mapped.pData)+size_t(y)*mapped.RowPitch, size_t(size.Width)*4);
                state->context->Unmap(state->staging.get(), 0);
                ++state->cpuReadbacks;
                if (state->frames.size() >= 128) { state->error = ERROR_BUFFER_OVERFLOW; return; }
                state->frames.push_back(std::move(frame));
            }
            catch (hresult_error const& error) { if (state->active) state->error = error.code().value; }
        });
        state->session = state->pool.CreateCaptureSession(item);
        state->session.IsCursorCaptureEnabled(false);
        current = state;
        state->session.StartCapture();
        return 0;
    }
    catch (hresult_error const& error) { return error.code().value; }
}
extern "C" __declspec(dllexport) int capture_start(uint64_t hwnd)
{
    return start_capture(hwnd, false);
}
extern "C" __declspec(dllexport) int capture_start_deferred(uint64_t hwnd)
{
    return start_capture(hwnd, true);
}
extern "C" __declspec(dllexport) void capture_pause()
{
    auto state = current;
    if (!state || !state->active.exchange(false)) return;
    state->pool.FrameArrived(state->token);
    state->session.Close();
    state->pool.Close();
}
extern "C" __declspec(dllexport) int capture_next(void* pixels, size_t capacity,
    int* width, int* height, int64_t* compositor100ns, int64_t* receivedQpc)
{
    auto state = current;
    if (!state) return -1;
    if (state->error) return state->error;
    std::lock_guard<std::mutex> lock(state->mutex);
    if (state->deferred && state->active) return 0;
    if (state->frames.empty()) return 0;
    auto& frame = state->frames.front();
    if (frame.texture)
    {
        try
        {
            if (!state->staging)
            {
                D3D11_TEXTURE2D_DESC desc;
                frame.texture->GetDesc(&desc);
                desc.Usage = D3D11_USAGE_STAGING;
                desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
                check_hresult(state->device->CreateTexture2D(&desc, nullptr, state->staging.put()));
            }
            state->context->CopyResource(state->staging.get(), frame.texture.get());
            D3D11_MAPPED_SUBRESOURCE mapped;
            check_hresult(state->context->Map(state->staging.get(), 0, D3D11_MAP_READ, 0, &mapped));
            frame.data.resize(size_t(frame.width)*frame.height*4);
            for (int y=0; y<frame.height; ++y)
                memcpy(frame.data.data()+size_t(y)*frame.width*4,
                    static_cast<uint8_t*>(mapped.pData)+size_t(y)*mapped.RowPitch, size_t(frame.width)*4);
            state->context->Unmap(state->staging.get(), 0);
            ++state->cpuReadbacks;
            frame.texture = nullptr;
        }
        catch (hresult_error const& error) { return error.code().value; }
    }
    if (frame.data.size() > capacity) return -2;
    *width = frame.width; *height = frame.height;
    *compositor100ns = frame.compositor; *receivedQpc = frame.received;
    memcpy(pixels, frame.data.data(), frame.data.size());
    state->frames.pop_front();
    return 1;
}
extern "C" __declspec(dllexport) void capture_stop()
{
    auto state = current;
    if (!state) return;
    capture_pause();
    current.reset();
}
extern "C" __declspec(dllexport) int capture_storage_state(int* gpuCopies, int* cpuReadbacks, int* retained)
{
    auto state = current;
    if (!state) return -1;
    std::lock_guard<std::mutex> lock(state->mutex);
    *gpuCopies = state->gpuCopies;
    *cpuReadbacks = state->cpuReadbacks;
    *retained = static_cast<int>(state->frames.size());
    return 0;
}
extern "C" __declspec(dllexport) int capture_resource_limits(int* fps, int* priority)
{
    try
    {
        check_hresult(capture_prepare());
        *fps = recordedFramesPerSecond;
        check_hresult(preparedDevice.as<IDXGIDevice>()->GetGPUThreadPriority(priority));
        return 0;
    }
    catch (hresult_error const& error) { return error.code().value; }
}
