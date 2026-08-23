import { lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "@/contexts/AuthContext";
import ProtectedRoute from "@/components/ProtectedRoute";
import Layout from "@/components/Layout";
import Login from "@/pages/Login";

const Dashboard = lazy(() => import("@/pages/Dashboard"));
const Discovery = lazy(() => import("@/pages/Discovery"));
const Analyze = lazy(() => import("@/pages/Analyze"));
const Tracking = lazy(() => import("@/pages/Tracking"));
const Competitors = lazy(() => import("@/pages/Competitors"));
const AIAnalysis = lazy(() => import("@/pages/AIAnalysis"));
const Reports = lazy(() => import("@/pages/Reports"));

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<ProtectedRoute><Layout /></ProtectedRoute>}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="discovery" element={<Discovery />} />
          <Route path="analyze" element={<Analyze />} />
          <Route path="tracking" element={<Tracking />} />
          <Route path="competitors" element={<Competitors />} />
          <Route path="analysis" element={<AIAnalysis />} />
          <Route path="reports" element={<Reports />} />
        </Route>
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </AuthProvider>
  );
}
