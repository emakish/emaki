// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#include "ui.hpp"
#include "core.hpp"
#include <QJsonDocument>
#include <QQmlExtensionPlugin>
#include <array>
#include <cerrno>
#include <cstring>
#include <qqml.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

using namespace emaki::sudo_channel;
SudoClient::SudoClient(QObject *parent) : QObject(parent) {
  const rlimit core{0, 0};
  memorySafe_ =
      prctl(PR_SET_DUMPABLE, 0) == 0 && setrlimit(RLIMIT_CORE, &core) == 0;
  handshake_.setSingleShot(true);
  handshake_.setInterval(3000);
  connect(&handshake_, &QTimer::timeout, this, &SudoClient::reset);
  retry_.setInterval(1000);
  connect(&retry_, &QTimer::timeout, this, &SudoClient::connectBroker);
  retry_.start();
  refresh_.setInterval(1000);
  connect(&refresh_, &QTimer::timeout, this, [this] {
    if (registered_)
      send(Type::Status);
  });
  refresh_.start();
  QTimer::singleShot(0, this, &SudoClient::connectBroker);
}
SudoClient::~SudoClient() {
  if (registered_)
    send(Type::Lock);
  reset();
}
void SudoClient::reset() {
  handshake_.stop();
  if (notifier_) {
    notifier_->setEnabled(false);
    notifier_->deleteLater();
  }
  notifier_ = nullptr;
  if (socket_ >= 0)
    close(socket_);
  socket_ = -1;
  registered_ = installed_ = acknowledged_ = btrfs_ = false;
  request_.clear();
  keys_.clear();
  emit requestChanged();
  emit keysChanged();
  emit stateChanged();
}
void SudoClient::setEnabled(bool enabled) {
  if (enabled_ == enabled)
    return;
  enabled_ = enabled;
  if (enabled)
    connectBroker();
  else
    reset();
  emit stateChanged();
}
void SudoClient::connectBroker() {
  if (!enabled_ || socket_ >= 0 || !memorySafe_ || getuid() == 0)
    return;
  const QByteArray path("/run/emaki-sudo/broker.sock");
  struct stat directory{}, endpoint{};
  if (lstat("/run/emaki-sudo", &directory) != 0 ||
      !S_ISDIR(directory.st_mode) || directory.st_uid != 0 ||
      (directory.st_mode & 0022) || lstat(path.constData(), &endpoint) != 0 ||
      !S_ISSOCK(endpoint.st_mode) || endpoint.st_uid != 0)
    return;
  sockaddr_un address{};
  address.sun_family = AF_UNIX;
  if (size_t(path.size()) >= sizeof(address.sun_path))
    return;
  memcpy(address.sun_path, path.constData(), size_t(path.size()));
  int fd = ::socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
  if (fd < 0)
    return;
  if (::connect(fd, reinterpret_cast<sockaddr *>(&address), sizeof(address)) !=
      0) {
    close(fd);
    return;
  }
  ucred credentials{};
  socklen_t length = sizeof(credentials);
  if (getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &credentials, &length) != 0 ||
      length != sizeof(credentials) || credentials.uid != 0 ||
      credentials.pid <= 1) {
    close(fd);
    return;
  }
  socket_ = fd;
  notifier_ = new QSocketNotifier(fd, QSocketNotifier::Read, this);
  connect(notifier_, &QSocketNotifier::activated, this, &SudoClient::receive);
  if (send(Type::HelloUi))
    handshake_.start();
}
bool SudoClient::send(Type type, quint64 id, quint32 option, const void *data,
                      size_t size) {
  if (socket_ < 0 || size > MaxBody)
    return false;
  Header header;
  header.type = type;
  header.request = id;
  header.option = option;
  header.size = quint32(size);
  iovec vectors[2]{{&header, sizeof(header)}, {const_cast<void *>(data), size}};
  msghdr message{};
  message.msg_iov = vectors;
  message.msg_iovlen = size ? 2 : 1;
  ssize_t count;
  do {
    count = sendmsg(socket_, &message, MSG_NOSIGNAL);
  } while (count < 0 && errno == EINTR);
  if (count != ssize_t(sizeof(header) + size)) {
    reset();
    return false;
  }
  return true;
}
void SudoClient::updateState(const QVariantMap &state) {
  if (!state.contains("keyAvailable"))
    return;
  installed_ = state.value("keyAvailable").toBool();
  acknowledged_ = state.value("warningAccepted").toBool();
  btrfs_ = state.value("btrfs").toBool();
  if (!state.contains("keys")) {
    emit stateChanged();
    return;
  }
  auto keys = state.value("keys").toList();
  for (auto &item : keys) {
    auto key = item.toMap();
    key.insert("label", key.value("requester"));
    item = key;
  }
  if (keys_ != keys) {
    keys_ = keys;
    emit keysChanged();
  }
  emit stateChanged();
}
void SudoClient::receive() {
  // Public metadata only. A password packet to this endpoint is a protocol
  // error.
  std::array<char, sizeof(Header) + MaxBody> buffer{};
  for (int batch = 0; batch < 32 && socket_ >= 0; ++batch) {
    ssize_t count =
        recv(socket_, buffer.data(), buffer.size(), MSG_DONTWAIT | MSG_TRUNC);
    if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
      return;
    if (count < ssize_t(sizeof(Header)) || count > ssize_t(buffer.size())) {
      reset();
      return;
    }
    Header header;
    memcpy(&header, buffer.data(), sizeof(header));
    if (header.magic != Magic || header.version != Version || header.reserved ||
        (header.type != Type::Finished && header.option) ||
        header.size != count - sizeof(header)) {
      reset();
      return;
    }
    if (header.type == Type::Finished) {
      if (header.size || header.option > 2 || !header.request) {
        reset();
        return;
      }
      if (request_.value("id").toString() == QString::number(header.request)) {
        if (header.option == 2) {
          emit authenticationFailed();
          const QString failedId = QString::number(header.request);
          QTimer::singleShot(450, this, [this, failedId] {
            if (request_.value("id").toString() == failedId) {
              request_.clear();
              emit requestChanged();
            }
          });
        } else {
          request_.clear();
          emit requestChanged();
        }
      }
      continue;
    }
    if (header.type != Type::Request && header.type != Type::Keys &&
        header.type != Type::Response && header.type != Type::Error) {
      reset();
      return;
    }
    QJsonParseError error;
    const auto document = QJsonDocument::fromJson(
        QByteArray(buffer.data() + sizeof(header), header.size), &error);
    if (error.error != QJsonParseError::NoError || !document.isObject()) {
      reset();
      return;
    }
    auto state = document.toVariant().toMap();
    if (header.type == Type::Error) {
      emit rejected();
      reset();
      return;
    }
    if (header.type == Type::Response && !registered_) {
      if (!state.value("ok").toBool()) {
        reset();
        return;
      }
      handshake_.stop();
      registered_ = true;
      emit stateChanged();
      send(locked_ ? Type::Lock : Type::Unlock);
      send(Type::Status);
    }
    if (header.type == Type::Request) {
      if (locked_) {
        send(Type::Cancel, header.request);
        continue;
      }
      if (state.value("id").toString() != QString::number(header.request)) {
        reset();
        return;
      }
      // Visible copy is supplied by the QML adapter from this factual metadata.
      state.insert("accountName", state.value("user"));
      request_ = state;
      emit requestChanged();
    }
    updateState(state);
  }
}
void SudoClient::submit(const QString &id, const QString &password,
                        const QString &mode, int seconds) {
  bool validId = false;
  const auto request = id.toULongLong(&validId);
  if (!registered_ || locked_ || !validId ||
      request_.value("id").toString() != id)
    return;
  quint32 option = 0;
  if (mode == "session")
    option = Session;
  else if (mode == "time" && seconds >= 0 &&
           validTimeDuration(quint32(seconds)))
    option = quint32(seconds);
  else if (mode.startsWith("turns:")) {
    bool validTurns = false;
    const auto turns = mode.mid(6).toUInt(&validTurns);
    if (!validTurns || mode != "turns:" + QString::number(turns) ||
        !request_.value("turnEligible").toBool())
      return;
    if (turns < MinTurns || turns > MaxTurns)
      return;
    option = TurnsFlag | turns;
  } else if (mode != "none")
    return;
  if (option && (!installed_ || !acknowledged_ ||
                 !request_.value("keyEligible").toBool()))
    return;
  // Qt owns the input string. Erase the one native conversion after copying it
  // into a locked, excluded-from-dump transport buffer; never retain a QString.
  auto bytes = password.toUtf8();
  const auto size = size_t(bytes.size());
  const size_t capacity = size_t(sysconf(_SC_PAGESIZE));
  void *memory = mmap(nullptr, capacity, PROT_READ | PROT_WRITE,
                      MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  bool safe = memory != MAP_FAILED;
  if (safe)
    safe = mlock(memory, capacity) == 0 &&
           madvise(memory, capacity, MADV_DONTDUMP) == 0;
  bool valid = size > 0 && size <= MaxPassword && !bytes.contains('\0') &&
               !bytes.contains('\n') && !bytes.contains('\r');
  if (safe && valid)
    memcpy(memory, bytes.constData(), size);
  explicit_bzero(bytes.data(), size);
  bytes.clear();
  if (safe && valid)
    send(Type::Submit, request, option, memory, size);
  if (memory != MAP_FAILED) {
    explicit_bzero(memory, capacity);
    munlock(memory, capacity);
    munmap(memory, capacity);
  }
  if (!safe || !valid)
    emit rejected();
}
void SudoClient::cancel(const QString &id) {
  bool valid = false;
  const auto request = id.toULongLong(&valid);
  if (registered_ && valid && request)
    send(Type::Cancel, request);
  if (request_.value("id").toString() == id) {
    request_.clear();
    emit requestChanged();
  }
}
void SudoClient::revoke(const QString &id) {
  bool valid = false;
  const auto key = id.toULongLong(&valid);
  if (valid && key)
    revokeKey(Type::Revoke, key);
}
void SudoClient::revokeAll() {
  revokeKey(Type::RevokeAll);
}
void SudoClient::revokeKey(Type type, quint64 id) {
  if (registered_ && send(type, id))
    return;
  // Revocation needs only peer credentials, never provider registration. A
  // separate connection also works while the provider handshake is pending.
  auto channel = emaki::sudo_channel::connectBroker();
  if (channel)
    sendPacket(channel.get(), type, id);
}
void SudoClient::setLocked(bool locked) {
  locked_ = locked;
  if (registered_)
    send(locked ? Type::Lock : Type::Unlock);
  if (locked && !request_.isEmpty()) {
    request_.clear();
    emit requestChanged();
  }
}
void SudoClient::acknowledgeWarning() {
  if (registered_ && installed_)
    send(Type::AcknowledgeWarning);
}

class SudoPlugin final : public QQmlExtensionPlugin {
  Q_OBJECT
  Q_PLUGIN_METADATA(IID QQmlExtensionInterface_iid)
public:
  void registerTypes(const char *uri) override {
    qmlRegisterType<SudoClient>(uri, 1, 0, "SudoClient");
  }
};
#include "ui.moc"
