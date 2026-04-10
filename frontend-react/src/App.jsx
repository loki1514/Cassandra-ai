import React from 'react';
import { createBrowserRouter, RouterProvider } from 'react-router-dom';
import AppOrb from './components/AppOrb';
import ConsoleLayout from './pages/ConsoleLayout';
import ConsoleDashboard from './pages/ConsoleDashboard';

const router = createBrowserRouter([
  {
    path: '/',
    element: <AppOrb />,
  },
  {
    path: '/console',
    element: <ConsoleLayout />,
    children: [
      { index: true, element: <ConsoleDashboard /> },
      { path: 'dashboard', element: <ConsoleDashboard /> },
    ],
  },
]);

export default function App() {
  return <RouterProvider router={router} />;
}
