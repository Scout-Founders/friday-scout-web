import { initializeApp } from "firebase/app";
import { getFirestore } from "firebase/firestore";

const firebaseConfig = {
  apiKey: "AIzaSyCPhCEcGrFFadwexSgd-YVu3WG3EWWzadI",
  authDomain: "scout-493918.firebaseapp.com",
  projectId: "scout-493918",
  storageBucket: "scout-493918.firebasestorage.app",
  messagingSenderId: "366650727885",
  appId: "1:366650727885:web:a242de055f18bc18153884",
  measurementId: "G-9FZ58QF8F9"
};

const app = initializeApp(firebaseConfig);
export const db = getFirestore(app);
