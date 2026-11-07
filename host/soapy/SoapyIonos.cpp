// SPDX-License-Identifier: MIT
//
// SoapyIonos.cpp — SoapySDR module for ionos-maximus (libionos, mode A)
//   Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
//
// Device args:
//   driver=ionos
//   backend=spi|replay         default spi (replay when 'path' is a regular file)
//   path=/dev/spidev0.0|FILE
//   spi_hz=4000000             SCLK; the maximum is measurement M1
//   drdy=gpiochip0:25          DRDY line (rev B radio 2: gpiochip0:4), or none
//   cmd=/dev/ttyAMA0           cmdlink tty to the FG23 (tune / decimation)
//   realtime=1                 replay: pace to the sample rate
//   loop=1                     replay: wrap at EOF
//
// Samples: one RX channel, CS16 native (I,Q int16 as in IQB2), CF32 by
// conversion. Sample rate = fs_in / decimation; the decimation is sent to
// the FG23 as 'i<R>' (I/Q firmware command) and the frequency as 'F<kHz>'.
// Rates are DISCRETE (fs_in 1 Msps at the 270 kHz PHY, R = 8..4096 in
// steps of 8); the actual rate comes from the IQB2 headers and is what
// getSampleRate() returns once blocks flow. The host-sustainable maximum
// over SPI is to be measured (M1, M4, M5); until then 125 ksps is the
// advertised ceiling, with 500 ksps "expected" per the README rate budget.

#include <SoapySDR/Device.hpp>
#include <SoapySDR/Registry.hpp>
#include <SoapySDR/Formats.hpp>
#include <SoapySDR/Logger.hpp>
#include <SoapySDR/Time.hpp>

#include "ionos.h"

#include <cstring>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>
#include <sys/stat.h>

namespace {

constexpr double FS_IN_DEFAULT = 1000000.0;   // 270 kHz acquisition BW PHY (measured 1 Msps)
constexpr double BW_MAX_HZ = 270000.0;
constexpr double HOST_MAX_SPS = 500000.0;     // README: int16 realistic up to ~500 ksps; to be measured (M4)

std::string argOr(const SoapySDR::Kwargs &a, const char *k, const std::string &d)
{
  auto it = a.find(k);
  return it == a.end() ? d : it->second;
}

bool isRegularFile(const std::string &p)
{
  struct stat st;
  return ::stat(p.c_str(), &st) == 0 && S_ISREG(st.st_mode);
}

} // namespace

class SoapyIonos : public SoapySDR::Device
{
public:
  explicit SoapyIonos(const SoapySDR::Kwargs &args)
  {
    ionos_config_init(&cfg_);
    path_ = argOr(args, "path", "/dev/spidev0.0");
    backend_ = argOr(args, "backend", isRegularFile(path_) ? "replay" : "spi");
    cmd_ = argOr(args, "cmd", "");
    drdy_ = argOr(args, "drdy", "gpiochip0:25");
    cfg_.backend = backend_.c_str();
    cfg_.path = path_.c_str();
    cfg_.spi_hz = static_cast<uint32_t>(std::stoul(argOr(args, "spi_hz", "4000000")));
    cfg_.cmd_path = cmd_.empty() ? nullptr : cmd_.c_str();
    cfg_.replay_realtime = argOr(args, "realtime", "1") != "0";
    cfg_.replay_loop = argOr(args, "loop", "0") != "0";
    cfg_.ring_blocks = 512;
    if (drdy_ == "none") {
      cfg_.drdy_chip = nullptr;
    } else {
      auto c = drdy_.find(':');
      drdyChip_ = drdy_.substr(0, c);
      cfg_.drdy_chip = drdyChip_.c_str();
      cfg_.drdy_line = (c == std::string::npos) ? 25 : std::stoi(drdy_.substr(c + 1));
    }
    int rc = ionos_open(&cfg_, &dev_);
    if (rc != IONOS_OK) {
      throw std::runtime_error(std::string("ionos_open(") + path_ + "): " + ionos_strerror(rc));
    }
    SoapySDR::logf(SOAPY_SDR_INFO, "SoapyIonos: %s backend on %s", backend_.c_str(), path_.c_str());
  }

  ~SoapyIonos() override { if (dev_) ionos_close(dev_); }

  // ---- identification ----
  std::string getDriverKey() const override { return "ionos"; }
  std::string getHardwareKey() const override { return "ionos-maximus FG23"; }
  SoapySDR::Kwargs getHardwareInfo() const override
  {
    SoapySDR::Kwargs k;
    k["backend"] = backend_;
    k["path"] = path_;
    k["libionos"] = ionos_version();
    k["link_mode"] = "A (Pi SPI main, FG23 secondary)";
    return k;
  }

  // ---- channels ----
  size_t getNumChannels(const int dir) const override { return dir == SOAPY_SDR_RX ? 1 : 0; }
  bool getFullDuplex(const int, const size_t) const override { return false; }

  // ---- stream ----
  std::vector<std::string> getStreamFormats(const int, const size_t) const override
  {
    return { SOAPY_SDR_CS16, SOAPY_SDR_CF32 };
  }
  std::string getNativeStreamFormat(const int, const size_t, double &fullScale) const override
  {
    fullScale = 32767.0;
    return SOAPY_SDR_CS16;
  }
  SoapySDR::ArgInfoList getStreamArgsInfo(const int, const size_t) const override { return {}; }

  SoapySDR::Stream *setupStream(const int direction, const std::string &format,
                                const std::vector<size_t> &channels,
                                const SoapySDR::Kwargs &) override
  {
    if (direction != SOAPY_SDR_RX) throw std::runtime_error("SoapyIonos: RX only");
    if (channels.size() > 1 || (channels.size() == 1 && channels[0] != 0))
      throw std::runtime_error("SoapyIonos: one RX channel");
    if (format == SOAPY_SDR_CS16) cf32_ = false;
    else if (format == SOAPY_SDR_CF32) cf32_ = true;
    else throw std::runtime_error("SoapyIonos: format " + format + " not supported");
    return reinterpret_cast<SoapySDR::Stream *>(this);
  }
  void closeStream(SoapySDR::Stream *) override { ionos_stop(dev_); }
  size_t getStreamMTU(SoapySDR::Stream *) const override { return 4096; }

  int activateStream(SoapySDR::Stream *, const int, const long long, const size_t) override
  {
    if (!cmd_.empty()) {
      ionos_command(dev_, ("i" + std::to_string(decim_)).c_str());
    }
    int rc = ionos_start(dev_);
    return (rc == IONOS_OK || rc == IONOS_E_STATE) ? 0 : SOAPY_SDR_STREAM_ERROR;
  }
  int deactivateStream(SoapySDR::Stream *, const int, const long long) override
  {
    ionos_stop(dev_);
    return 0;
  }

  int readStream(SoapySDR::Stream *, void *const *buffs, const size_t numElems,
                 int &flags, long long &timeNs, const long timeoutUs) override
  {
    flags = 0;
    size_t want = numElems > 16384 ? 16384 : numElems;
    scratch_.resize(want * 2);
    int n = ionos_read_iq(dev_, scratch_.data(), want, static_cast<int>(timeoutUs / 1000));
    if (n == 0) return SOAPY_SDR_TIMEOUT;
    if (n == IONOS_E_EOF) return SOAPY_SDR_END_BURST;
    if (n < 0) return SOAPY_SDR_STREAM_ERROR;

    ionos_stats_t st; ionos_stats(dev_, &st);
    if (st.lost_blocks != lastLost_ || st.ring_overflows != lastOvf_) {
      lastLost_ = st.lost_blocks; lastOvf_ = st.ring_overflows;
      SoapySDR::logf(SOAPY_SDR_WARNING, "SoapyIonos: lost_blocks=%llu ring_overflows=%llu",
                     (unsigned long long)st.lost_blocks, (unsigned long long)st.ring_overflows);
      flags |= SOAPY_SDR_HAS_TIME;   // signal a discontinuity through a fresh timestamp
    }
    double sps = st.sample_rate_hz ? st.sample_rate_hz : currentRate();
    timeNs = SoapySDR::ticksToTimeNs(samplesOut_, sps);
    samplesOut_ += static_cast<long long>(n);
    flags |= SOAPY_SDR_HAS_TIME;

    if (cf32_) {
      float *out = static_cast<float *>(buffs[0]);
      const float k = 1.0f / 32768.0f;
      for (int i = 0; i < n * 2; i++) out[i] = scratch_[i] * k;
    } else {
      std::memcpy(buffs[0], scratch_.data(), static_cast<size_t>(n) * 4);
    }
    return n;
  }

  // ---- antenna / gain (none on the FG23 beyond the PHY) ----
  std::vector<std::string> listAntennas(const int, const size_t) const override { return { "RX" }; }
  void setAntenna(const int, const size_t, const std::string &) override {}
  std::string getAntenna(const int, const size_t) const override { return "RX"; }
  std::vector<std::string> listGains(const int, const size_t) const override { return {}; }
  bool hasGainMode(const int, const size_t) const override { return true; }
  void setGainMode(const int, const size_t, const bool) override {}
  bool getGainMode(const int, const size_t) const override { return true; }   // RAIL AGC

  // ---- frequency ----
  std::vector<std::string> listFrequencies(const int, const size_t) const override { return { "RF" }; }
  SoapySDR::RangeList getFrequencyRange(const int, const size_t, const std::string &) const override
  {
    // FG23 synth range; the BRD4265B matching network is 868/915 MHz
    return { SoapySDR::Range(100e6, 1000e6) };
  }
  void setFrequency(const int, const size_t, const std::string &name, const double f,
                    const SoapySDR::Kwargs &) override
  {
    if (name != "RF") return;
    freq_ = f;
    if (!cmd_.empty()) ionos_command(dev_, ("F" + std::to_string(static_cast<long>(f / 1000.0 + 0.5))).c_str());
  }
  double getFrequency(const int, const size_t, const std::string &) const override { return freq_; }

  // ---- sample rate ----
  SoapySDR::RangeList getSampleRateRange(const int, const size_t) const override
  {
    // fs_in / R, R multiple of 8 in 8..4096. Host-side maximum to be measured (M4):
    // the README budget says int16 is realistic to ~500 ksps on one SPI lane.
    return { SoapySDR::Range(FS_IN_DEFAULT / 4096.0, HOST_MAX_SPS) };
  }
  std::vector<double> listSampleRates(const int, const size_t) const override
  {
    std::vector<double> v;
    for (unsigned r : { 4096u, 2048u, 1024u, 512u, 256u, 128u, 80u, 64u, 40u, 32u, 20u, 16u, 8u }) {
      double sps = FS_IN_DEFAULT / r;
      if (sps <= HOST_MAX_SPS) v.push_back(sps);
    }
    return v;
  }
  void setSampleRate(const int, const size_t, const double rate) override
  {
    unsigned r = static_cast<unsigned>(FS_IN_DEFAULT / rate + 0.5);
    r = (r / 8u) * 8u;
    if (r < 8u) r = 8u;
    if (r > 4096u) r = 4096u;
    decim_ = r;
    if (!cmd_.empty()) ionos_command(dev_, ("i" + std::to_string(decim_)).c_str());
  }
  double getSampleRate(const int, const size_t) const override { return currentRate(); }

  // ---- bandwidth ----
  SoapySDR::RangeList getBandwidthRange(const int, const size_t) const override
  {
    return { SoapySDR::Range(1000.0, BW_MAX_HZ) };
  }
  double getBandwidth(const int, const size_t) const override
  {
    double sps = currentRate();
    return sps < BW_MAX_HZ ? sps : BW_MAX_HZ;
  }

  // ---- settings ----
  SoapySDR::ArgInfoList getSettingInfo() const override
  {
    SoapySDR::ArgInfoList l;
    SoapySDR::ArgInfo a;
    a.key = "cmd"; a.name = "FG23 command"; a.type = SoapySDR::ArgInfo::STRING;
    a.description = "Raw one-line command sent over cmdlink (e.g. F868950, i32, s)";
    l.push_back(a);
    return l;
  }
  void writeSetting(const std::string &key, const std::string &value) override
  {
    if (key == "cmd" && !cmd_.empty()) ionos_command(dev_, value.c_str());
  }
  std::string readSetting(const std::string &key) const override
  {
    ionos_stats_t st; ionos_stats(dev_, &st);
    if (key == "lost_blocks") return std::to_string(st.lost_blocks);
    if (key == "ring_overflows") return std::to_string(st.ring_overflows);
    if (key == "blocks") return std::to_string(st.blocks);
    if (key == "fs_in_hz") return std::to_string(st.fs_in_hz);
    if (key == "decim") return std::to_string(st.decim);
    return "";
  }

private:
  double currentRate() const
  {
    ionos_stats_t st; ionos_stats(dev_, &st);
    if (st.sample_rate_hz) return st.sample_rate_hz;
    return FS_IN_DEFAULT / decim_;
  }

  ionos_config_t cfg_{};
  ionos_dev_t *dev_ = nullptr;
  std::string path_, backend_, cmd_, drdy_, drdyChip_;
  bool cf32_ = false;
  unsigned decim_ = 8;             // 125 ksps at 1 Msps fs_in
  double freq_ = 868.95e6;
  long long samplesOut_ = 0;
  uint64_t lastLost_ = 0, lastOvf_ = 0;
  std::vector<int16_t> scratch_;
};

// ---- registry ----

static SoapySDR::KwargsList findIonos(const SoapySDR::Kwargs &args)
{
  SoapySDR::KwargsList results;
  std::string path = argOr(args, "path", "");
  if (!path.empty()) {
    SoapySDR::Kwargs d = args;
    d["label"] = "ionos-maximus (" + (isRegularFile(path) ? std::string("replay ") : std::string("")) + path + ")";
    results.push_back(d);
    return results;
  }
  for (const char *dev : { "/dev/spidev0.0", "/dev/spidev0.1" }) {
    struct stat st;
    if (::stat(dev, &st) == 0) {
      SoapySDR::Kwargs d = args;
      d["path"] = dev;
      d["backend"] = "spi";
      d["label"] = std::string("ionos-maximus FG23 on ") + dev;
      results.push_back(d);
    }
  }
  return results;
}

static SoapySDR::Device *makeIonos(const SoapySDR::Kwargs &args)
{
  return new SoapyIonos(args);
}

static SoapySDR::Registry registerIonos("ionos", &findIonos, &makeIonos, SOAPY_SDR_ABI_VERSION);
