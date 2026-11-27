# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Faux paho.mqtt.client et petit broker en mémoire, pour les tests.

    FakeMqttModule              enregistre seulement les appels (pas de broker)
    broker = FakeBroker()
    module = make_module(broker) même interface, messages routés par le broker
    broker.pump()               livre les messages en attente (synchrone)
    broker.kill(client)         disparition brutale : le testament est publié

Le broker garde les messages retenus et les livre à l'abonnement, comme
mosquitto ; il ne simule ni la durée de maintien ni la QoS.
"""

from collections import deque


def topic_matches(pattern, topic):
    p, t = pattern.split("/"), topic.split("/")
    for i, part in enumerate(p):
        if part == "#":
            return True
        if i >= len(t) or (part != "+" and part != t[i]):
            return False
    return len(p) == len(t)


class Message:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload


class FakeBroker:
    def __init__(self):
        self.clients = []
        self.retained = {}
        self._queue = deque()
        self.log = []                       # (sujet, contenu) de tout ce qui a été publié

    def connect(self, client):
        if client not in self.clients:
            self.clients.append(client)
        self._queue.append(("connect", client))

    def disconnect(self, client):
        if client in self.clients:
            self.clients.remove(client)

    def kill(self, client):
        """Client disparu sans se déconnecter : son testament est publié."""
        self.disconnect(client)
        if client.will is not None:
            topic, payload, retain = client.will
            self.route(topic, payload, retain)

    def subscribe(self, client, pattern):
        for topic, payload in self.retained.items():
            if topic_matches(pattern, topic):
                self._queue.append(("deliver", client, topic, payload))

    def route(self, topic, payload, retain=False):
        if isinstance(payload, str):
            payload = payload.encode()
        payload = bytes(payload)
        self.log.append((topic, payload))
        if retain:
            self.retained[topic] = payload
        for client in list(self.clients):
            if any(topic_matches(p, topic) for p in client.subscriptions):
                self._queue.append(("deliver", client, topic, payload))

    def pump(self):
        """Livre tout ce qui est en attente ; renvoie le nombre de livraisons."""
        n = 0
        while self._queue:
            item = self._queue.popleft()
            n += 1
            if item[0] == "connect":
                client = item[1]
                if client.on_connect:
                    client.on_connect(client, None, {}, 0, None)
            else:
                _, client, topic, payload = item
                if client in self.clients and client.on_message:
                    client.on_message(client, None, Message(topic, payload))
        return n


def make_module(broker=None):
    """Objet qui imite le module paho.mqtt.client (paho-mqtt 2)."""

    class CallbackAPIVersion:
        VERSION1 = "v1"
        VERSION2 = "v2"

    class Client:
        instances = []

        def __init__(self, api, client_id=""):
            self.api = api
            self.client_id = client_id
            self.calls = []
            self.subscriptions = []
            self.will = None
            self.on_connect = None
            self.on_message = None
            Client.instances.append(self)

        def will_set(self, topic, payload=None, qos=0, retain=False):
            self.calls.append(("will_set", topic, payload, qos, retain))
            self.will = (topic, payload, retain)

        def reconnect_delay_set(self, a, b):
            self.calls.append(("reconnect_delay_set", a, b))

        def connect_async(self, host, port=1883, keepalive=60):
            self.calls.append(("connect_async", host, port, keepalive))
            if broker is not None:
                broker.connect(self)

        def loop_start(self):
            self.calls.append(("loop_start",))

        def loop_stop(self):
            self.calls.append(("loop_stop",))

        def subscribe(self, topics):
            self.calls.append(("subscribe", topics))
            if isinstance(topics, str):
                topics = [(topics, 0)]
            for pattern, _qos in topics:
                self.subscriptions.append(pattern)
                if broker is not None:
                    broker.subscribe(self, pattern)

        def publish(self, topic, payload, qos=0, retain=False):
            self.calls.append(("publish", topic, payload, qos, retain))
            if broker is not None and self in broker.clients:
                broker.route(topic, payload, retain)

        def disconnect(self):
            self.calls.append(("disconnect",))
            if broker is not None:
                broker.disconnect(self)

    class Module:
        pass

    Module.CallbackAPIVersion = CallbackAPIVersion
    Module.Client = Client
    return Module


FakeMqttModule = make_module()
