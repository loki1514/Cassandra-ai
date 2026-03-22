import React from 'react';

export default function RoleSelector({ currentRole, roles, onSwitch }) {
  if (!roles || roles.length === 0) return null;
  return (
    <select 
      value={currentRole} 
      onChange={e => onSwitch(e.target.value)}
      className="role-selector btn-role"
      style={{
        background: 'rgba(0,0,0,0.5)',
        border: '1px solid currentColor',
        color: 'inherit',
        padding: '10px 15px',
        borderRadius: '20px',
        fontSize: '14px',
        marginLeft: '10px',
        cursor: 'pointer'
      }}
    >
      {roles.map(r => (
        <option key={r.id} value={r.id} style={{ background: '#000' }}>
          {r.name}
        </option>
      ))}
    </select>
  );
}
