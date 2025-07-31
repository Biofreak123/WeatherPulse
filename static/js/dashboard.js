// Dashboard JavaScript functionality

// Global variables
let refreshInterval;
let isRefreshing = false;

// Initialize dashboard when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    initializeDashboard();
    setupEventListeners();
    startAutoRefresh();
});

function initializeDashboard() {
    console.log('Dashboard initialized');
    
    // Initialize tooltips
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    var tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
    
    // Load initial data
    updateDashboardData();
}

function setupEventListeners() {
    // Add any specific event listeners here
    document.addEventListener('visibilitychange', function() {
        if (document.hidden) {
            stopAutoRefresh();
        } else {
            startAutoRefresh();
        }
    });
}

function startAutoRefresh() {
    if (refreshInterval) {
        clearInterval(refreshInterval);
    }
    
    // Refresh every 30 seconds
    refreshInterval = setInterval(function() {
        if (!isRefreshing) {
            updateDashboardData();
        }
    }, 30000);
}

function stopAutoRefresh() {
    if (refreshInterval) {
        clearInterval(refreshInterval);
        refreshInterval = null;
    }
}

function updateDashboardData() {
    if (isRefreshing) return;
    
    isRefreshing = true;
    
    // Update statistics
    fetch('/api/stats')
        .then(response => response.json())
        .then(data => {
            if (!data.error) {
                updateStatistics(data);
                updateConnectionStatus(data.is_connected, data.connection_msg);
            }
        })
        .catch(error => {
            console.error('Error fetching stats:', error);
        });
    
    // Update recent orders
    fetch('/api/orders')
        .then(response => response.json())
        .then(data => {
            if (Array.isArray(data)) {
                updateRecentOrders(data.slice(0, 10)); // Show only 10 most recent
            }
        })
        .catch(error => {
            console.error('Error fetching orders:', error);
        })
        .finally(() => {
            isRefreshing = false;
        });
}

function updateStatistics(data) {
    const elements = {
        'total-orders': data.total_orders || 0,
        'successful-orders': data.successful_orders || 0,
        'failed-orders': data.failed_orders || 0,
        'pending-orders': data.pending_orders || 0
    };
    
    Object.entries(elements).forEach(([id, value]) => {
        const element = document.getElementById(id);
        if (element) {
            animateCounter(element, value);
        }
    });
}

function updateConnectionStatus(isConnected, message) {
    // Update connection status if elements exist
    const statusIcon = document.querySelector('.connection-status-icon');
    const statusText = document.querySelector('.connection-status-text');
    const statusMessage = document.querySelector('.connection-status-message');
    
    if (statusIcon) {
        statusIcon.className = isConnected ? 
            'fas fa-check-circle text-success fs-4 connection-status-icon' : 
            'fas fa-exclamation-triangle text-warning fs-4 connection-status-icon';
    }
    
    if (statusText) {
        statusText.textContent = isConnected ? 'API Connection Active' : 'API Connection Issue';
    }
    
    if (statusMessage) {
        statusMessage.textContent = message;
    }
}

function updateRecentOrders(orders) {
    const tableBody = document.getElementById('recent-orders-table');
    if (!tableBody) return;
    
    if (orders.length === 0) {
        tableBody.innerHTML = `
            <tr>
                <td colspan="6" class="text-center py-4">
                    <i class="fas fa-inbox fs-4 text-muted mb-2"></i>
                    <p class="mb-0 text-muted">No recent orders</p>
                </td>
            </tr>
        `;
        return;
    }
    
    tableBody.innerHTML = orders.map(order => {
        const createdAt = order.created_at ? 
            new Date(order.created_at).toLocaleDateString('en-US', {
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit'
            }) : 'N/A';
        
        const signalBadge = order.signal === 'CALL' ? 
            `<span class="badge bg-success"><i class="fas fa-arrow-up me-1"></i>${order.signal}</span>` :
            `<span class="badge bg-danger"><i class="fas fa-arrow-down me-1"></i>${order.signal}</span>`;
        
        const statusBadge = getStatusBadge(order.order_status);
        
        return `
            <tr>
                <td><small>${createdAt}</small></td>
                <td>${signalBadge}</td>
                <td><strong>${order.ticker}</strong></td>
                <td><small class="font-monospace">${order.contract_symbol || 'N/A'}</small></td>
                <td>${order.quantity}</td>
                <td>${statusBadge}</td>
            </tr>
        `;
    }).join('');
}

function getStatusBadge(status) {
    switch (status) {
        case 'submitted':
            return '<span class="badge bg-success"><i class="fas fa-check me-1"></i>Success</span>';
        case 'failed':
            return '<span class="badge bg-danger"><i class="fas fa-times me-1"></i>Failed</span>';
        case 'processing':
            return '<span class="badge bg-warning"><i class="fas fa-clock me-1"></i>Processing</span>';
        default:
            return `<span class="badge bg-secondary">${status}</span>`;
    }
}

function animateCounter(element, targetValue) {
    const currentValue = parseInt(element.textContent) || 0;
    const increment = targetValue > currentValue ? 1 : -1;
    const duration = 1000; // 1 second
    const steps = Math.abs(targetValue - currentValue);
    const stepDuration = steps > 0 ? duration / steps : 0;
    
    if (steps === 0) return;
    
    let current = currentValue;
    const timer = setInterval(() => {
        current += increment;
        element.textContent = current;
        
        if (current === targetValue) {
            clearInterval(timer);
        }
    }, stepDuration);
}

// Utility functions
function showNotification(message, type = 'info') {
    // Create notification element
    const notification = document.createElement('div');
    notification.className = `alert alert-${type} alert-dismissible fade show position-fixed`;
    notification.style.cssText = 'top: 20px; right: 20px; z-index: 9999; min-width: 300px;';
    notification.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    
    document.body.appendChild(notification);
    
    // Auto-remove after 5 seconds
    setTimeout(() => {
        if (notification.parentNode) {
            notification.remove();
        }
    }, 5000);
}

function formatCurrency(amount) {
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD'
    }).format(amount);
}

function formatDate(dateString) {
    if (!dateString) return 'N/A';
    return new Date(dateString).toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

// Export functions for use in other scripts
window.dashboard = {
    updateDashboardData,
    showNotification,
    formatCurrency,
    formatDate,
    startAutoRefresh,
    stopAutoRefresh
};
