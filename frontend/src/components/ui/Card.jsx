export default function Card({ children, className = '', ...props }) {
  return (
    <div
      className={`bg-slate-800 border border-slate-700 rounded-xl p-6 ${className}`}
      {...props}
    >
      {children}
    </div>
  )
}
