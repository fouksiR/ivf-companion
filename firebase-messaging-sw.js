/* Firebase Cloud Messaging Service Worker — Melod·AI */
importScripts('https://www.gstatic.com/firebasejs/9.23.0/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/9.23.0/firebase-messaging-compat.js');

firebase.initializeApp({
  apiKey: "AIzaSyDfqkNkezFiO7qcXELKwzzwoK3kLvqdOFw",
  authDomain: "fertility-gp-portal.firebaseapp.com",
  databaseURL: "https://fertility-gp-portal-default-rtdb.asia-southeast1.firebasedatabase.app",
  projectId: "fertility-gp-portal",
  storageBucket: "fertility-gp-portal.appspot.com",
  messagingSenderId: "532857641879",
  appId: "1:532857641879:web:226d22b6b33987ce7e82d2"
});

var messaging = firebase.messaging();

messaging.onBackgroundMessage(function(payload) {
  var title = (payload.notification && payload.notification.title) || 'Melod\u00b7AI Reminder';
  var options = {
    body: (payload.notification && payload.notification.body) || 'Time for your medication',
    icon: '/static/egg-icon-192.png',
    badge: '/static/egg-badge-72.png',
    tag: 'med-reminder',
    vibrate: [200, 100, 200],
    actions: [{action: 'open', title: 'Open App'}]
  };
  return self.registration.showNotification(title, options);
});

self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  event.waitUntil(clients.openWindow('/'));
});
