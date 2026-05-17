import { Outlet } from 'react-router-dom'
import UserNavbar from '../components/UserNavbar'
import './UserLayout.css'

export default function UserLayout() {
  return (
    <div className="user-layout">
      <UserNavbar />
      <main className="user-main">
        <Outlet />
      </main>
    </div>
  )
}
