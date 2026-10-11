// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "protocol.hpp"
#include <QObject>
#include <QSocketNotifier>
#include <QTimer>
#include <QVariant>

class SudoClient : public QObject {
  Q_OBJECT
  Q_PROPERTY(bool enabled READ enabled WRITE setEnabled NOTIFY stateChanged)
  Q_PROPERTY(bool connected READ connected NOTIFY stateChanged)
  Q_PROPERTY(QVariantMap request READ request NOTIFY requestChanged)
  Q_PROPERTY(QVariantList keys READ keys NOTIFY keysChanged)
  Q_PROPERTY(bool installed READ installed NOTIFY stateChanged)
  Q_PROPERTY(bool warningAccepted READ warningAccepted NOTIFY stateChanged)
  Q_PROPERTY(bool btrfs READ btrfs NOTIFY stateChanged)
  Q_PROPERTY(
      bool snapguardAvailable READ snapguardAvailable NOTIFY stateChanged)
public:
  explicit SudoClient(QObject *parent = nullptr);
  ~SudoClient() override;
  bool enabled() const { return enabled_; }
  void setEnabled(bool enabled);
  bool connected() const { return registered_; }
  QVariantMap request() const { return request_; }
  QVariantList keys() const { return keys_; }
  bool installed() const { return installed_; }
  bool warningAccepted() const { return acknowledged_; }
  bool btrfs() const { return btrfs_; }
  bool snapguardAvailable() const { return false; }
  Q_INVOKABLE void submit(const QString &id, const QString &password,
                          const QString &mode, int seconds);
  Q_INVOKABLE void cancel(const QString &id);
  Q_INVOKABLE void revoke(const QString &id);
  Q_INVOKABLE void revokeAll();
  Q_INVOKABLE void setLocked(bool locked);
  Q_INVOKABLE void acknowledgeWarning();
signals:
  void stateChanged();
  void requestChanged();
  void keysChanged();
  void rejected();
  void authenticationFailed();

private:
  void connectBroker();
  void revokeKey(emaki::sudo_channel::Type type, quint64 id = 0);
  void receive();
  void reset();
  bool send(emaki::sudo_channel::Type type, quint64 id = 0, quint32 option = 0,
            const void *data = nullptr, size_t size = 0);
  void updateState(const QVariantMap &state);
  int socket_ = -1;
  QSocketNotifier *notifier_ = nullptr;
  QTimer retry_;
  QTimer handshake_;
  QTimer refresh_;
  bool enabled_ = false;
  bool registered_ = false;
  bool installed_ = false;
  bool acknowledged_ = false;
  bool btrfs_ = false;
  bool locked_ = true;
  bool memorySafe_ = false;
  QVariantMap request_;
  QVariantList keys_;
};
