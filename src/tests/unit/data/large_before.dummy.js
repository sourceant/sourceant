// original_script.js
// This file contains approximately 300 lines of JavaScript code for testing diff parsers.

// --- Global Constants ---
const PI = 3.14159;
const MAX_RETRIES = 5;
const APP_NAME = 'DiffTestApp';
const DEFAULT_TIMEOUT_MS = 3000;

// --- Utility Functions ---

/**
 * Generates a unique ID.
 * @returns {string} A unique ID string.
 */
function generateUniqueId() {
    return 'id_' + Math.random().toString(36).substr(2, 9) + Date.now();
}

/**
 * Formats a date object into a readable string.
 * @param {Date} date The date object to format.
 * @returns {string} Formatted date string.
 */
function formatDate(date) {
    const options = { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' };
    return date.toLocaleDateString('en-US', options);
}

/**
 * Capitalizes the first letter of a string.
 * @param {string} str The input string.
 * @returns {string} The capitalized string.
 */
function capitalizeFirstLetter(str) {
    if (!str) return '';
    return str.charAt(0).toUpperCase() + str.slice(1);
}

// --- Data Structures ---

class User {
    constructor(id, name, email) {
        this.id = id;
        this.name = name;
        this.email = email;
        this.createdAt = new Date();
    }

    getDetails() {
        return `User ID: ${this.id}, Name: ${this.name}, Email: ${this.email}, Created: ${formatDate(this.createdAt)}`;
    }

    updateEmail(newEmail) {
        if (!newEmail.includes('@')) {
            throw new Error('Invalid email format.');
        }
        this.email = newEmail;
        console.log(`Email for ${this.name} updated to ${this.email}`);
    }
}

class Product {
    constructor(id, name, price, stock) {
        this.id = id;
        this.name = name;
        this.price = price;
        this.stock = stock;
    }

    getDisplayPrice() {
        return `$${this.price.toFixed(2)}`;
    }

    isInStock() {
        return this.stock > 0;
    }

    decreaseStock(amount) {
        if (this.stock < amount) {
            throw new Error('Not enough stock.');
        }
        this.stock -= amount;
        console.log(`${this.name} stock decreased by ${amount}. New stock: ${this.stock}`);
    }
}

// --- Core Application Logic ---

const users = [];
const products = [];

function initData() {
    for (let i = 0; i < 10; i++) { // Reduced loop for brevity
        users.push(new User(generateUniqueId(), `User ${i}`, `user${i}@example.com`));
        products.push(new Product(generateUniqueId(), `Product ${i}`, (i + 1) * 10.50, i * 5));
    }
    console.log('Initial data loaded.');
}

function findUserById(id) {
    return users.find(user => user.id === id);
}

function findProductByName(name) {
    return products.find(product => product.name === name);
}

// --- Simulation Functions ---

function simulatePurchase(userId, productId, quantity) {
    const user = findUserById(userId);
    const product = products.find(p => p.id === productId);

    if (!user) {
        console.error('User not found.');
        return false;
    }
    if (!product) {
        console.error('Product not found.');
        return false;
    }
    if (!product.isInStock() || product.stock < quantity) {
        console.error('Product out of stock or insufficient quantity.');
        return false;
    }

    try {
        product.decreaseStock(quantity);
        console.log(`Purchase successful: ${quantity} x ${product.name} for ${user.name}`);
        return true;
    } catch (e) {
        console.error(`Purchase failed: ${e.message}`);
        return false;
    }
}

function processOrders(orderList) {
    console.log('--- Processing Orders ---');
    orderList.forEach(order => {
        const { userId, productId, quantity } = order;
        simulatePurchase(userId, productId, quantity);
    });
    console.log('--- Orders Processed ---');
}


// --- Placeholder lines for changes and padding ---
// These lines are deliberately simple and repetitive.
// Changes will be introduced at specific points within these blocks.
// Start of large repetitive block
for (let i = 0; i < 5; i++) { // Block 1 (Lines ~100-120)
    console.log(`Processing item ${i} in batch 1.`);
    const tempVar1 = i * 2;
    const tempVar2 = tempVar1 + 5;
    if (tempVar2 > 10) {
        console.log(`Condition met for item ${i}.`);
    } else {
        console.log(`Condition not met for item ${i}.`);
    }
    // Line for addition (Change 1: Line Addition)
}

function dummyFunctionA() { // Block 2 (Lines ~125-145)
    for (let j = 0; j < 5; j++) {
        let value = j * 3;
        value += 1;
        console.log(`Dummy A: ${value}`);
    }
}

for (let k = 0; k < 5; k++) { // Block 3 (Lines ~150-170)
    let result = k / 2;
    console.log(`Processing item ${k} in batch 3. Result: ${result}`);
    if (result > 5) {
        // Line for deletion (Change 2: Line Deletion)
    }
}

function dummyFunctionB() { // Block 4 (Lines ~175-195)
    for (let l = 0; l < 5; l++) {
        let data = { id: l, status: 'pending' };
        console.log(`Dummy B: ${JSON.stringify(data)}`);
    }
}

for (let m = 0; m < 5; m++) { // Block 5 (Lines ~200-220)
    let finalValue = m * m;
    console.log(`Processing item ${m} in batch 5. Final: ${finalValue}`);
    // Line for modification (Change 3: Line Modification)
    // Extra line in original, to be modified
}

function dummyFunctionC() { // Block 6 (Lines ~225-245)
    for (let n = 0; n < 5; n++) {
        let count = n + 100;
        console.log(`Dummy C: Count ${count}`);
    }
}

for (let p = 0; p < 5; p++) { // Block 7 (Lines ~250-270)
    let total = p + (p * 0.1);
    console.log(`Processing item ${p} in batch 7. Total: ${total}`);
    // Block for replacement (Change 4: Block Replacement)
    console.log("This entire block will be replaced.");
    console.log("It has multiple lines that will vanish.");
    console.log("And new lines will take its place.");
}

function dummyFunctionD() { // Block 8 (Lines ~275-295)
    for (let q = 0; q < 5; q++) {
        let flag = q % 2 === 0;
        console.log(`Dummy D: Flag ${flag}`);
    }
}

for (let r = 0; r < 5; r++) { // Block 9 (Lines ~300-320)
    let itemCode = `ITEM-${r}`;
    console.log(`Processing item ${r} in batch 9. Code: ${itemCode}`);
    // Line for comment change (Change 5: Comment Modification)
    // This is an important comment.
}

function dummyFunctionE() { // Block 10 (Lines ~325-345)
    for (let s = 0; s < 5; s++) {
        let state = s % 3;
        console.log(`Dummy E: State ${state}`);
    }
}

// Line for whitespace change (Change 6: Whitespace Only Change)
const whitespaceVar   =  123;

for (let t = 0; t < 5; t++) { // Block 11 (Lines ~350-370)
    let index = t * 10;
    console.log(`Processing item ${t} in batch 11. Index: ${index}`);
}

function dummyFunctionF() { // Block 12 (Lines ~375-395)
    for (let u = 0; u < 5; u++) {
        let category = `CAT-${u}`;
        console.log(`Dummy F: Category ${category}`);
    }
}

// Block for new function addition (Change 7: New Function Addition)
// The function will be added AFTER this comment block in the updated file.

for (let v = 0; v < 5; v++) { // Block 13 (Lines ~400-420)
    let counter = v + 1;
    console.log(`Processing item ${v} in batch 13. Counter: ${counter}`);
}

function dummyFunctionG() { // Block 14 (Lines ~425-445)
    for (let w = 0; w < 5; w++) {
        let status = `STAT-${w}`;
        console.log(`Dummy G: Status ${status}`);
    }
}

// Line for changing a constant (Change 8: Constant Value Change)
const OLD_CONSTANT_VALUE = 'old_value';

for (let x = 0; x < 5; x++) { // Block 15 (Lines ~450-470)
    let buffer = [];
    buffer.push(x);
    console.log(`Processing item ${x} in batch 15. Buffer: ${buffer}`);
}

function dummyFunctionH() { // Block 16 (Lines ~475-495)
    for (let y = 0; y < 5; y++) {
        let tempChar = String.fromCharCode(65 + y);
        console.log(`Dummy H: Char ${tempChar}`);
    }
}

// Block for multi-line comment addition (Change 9: Multi-line Comment Addition)
// This entire block will have a new multi-line comment added above it.

for (let z = 0; z < 5; z++) { // Block 17 (Lines ~500-520)
    let recordId = `REC-${z}`;
    console.log(`Processing item ${z} in batch 17. Record: ${recordId}`);
}

// --- Padding lines to reach ~300 lines ---
for (let i = 0; i < 15; i++) {
    console.log(`Padding line ${i}.`);
    if (i % 3 === 0) {
        console.log('  Small checkpoint.');
    }
}

// Final execution block
initData();
const userToPurchase = users[0].id;
const productToPurchase = products[0].id;
simulatePurchase(userToPurchase, productToPurchase, 1);

// Line to be removed at the very end (Change 10: Line Removed at EOF)
console.log("This line will be completely removed in the updated file.");
