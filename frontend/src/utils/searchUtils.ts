import { AggregationType, AggregationRequest } from '../api/models'
import { performAggregation } from '../api/api'

export const AGGREGATION_FIELDS = [
    'claimAmount',
    'lineItemCharge', 
    'amountPaid',
    'balanceDue'
]

const AVG_REGEX = /average|avg|mean|calculate average/i
const SUM_REGEX = /sum|total|add up|calculate total/i
const MIN_REGEX = /min|minimum|lowest|smallest/i
const MAX_REGEX = /max|maximum|highest|largest/i
const COUNT_REGEX = /count|how many|number of/i

export function detectAggregationQuery(query: string): { field: string, type: AggregationType } | null {
    let aggregationType: AggregationType | null = null
    let field: string | null = null

    if (AVG_REGEX.test(query)) {
        aggregationType = AggregationType.Average
    } else if (SUM_REGEX.test(query)) {
        aggregationType = AggregationType.Sum
    } else if (MIN_REGEX.test(query)) {
        aggregationType = AggregationType.Minimum
    } else if (MAX_REGEX.test(query)) {
        aggregationType = AggregationType.Maximum
    } else if (COUNT_REGEX.test(query)) {
        aggregationType = AggregationType.Count
    }

    if (!aggregationType) {
        return null
    }

    for (const possibleField of AGGREGATION_FIELDS) {
        const fieldRegex = new RegExp(possibleField.replace(/([A-Z])/g, ' $1').toLowerCase(), 'i')
        if (fieldRegex.test(query)) {
            field = possibleField
            break
        }
    }

    if (!field) {
        if (/claim|claims|total claim/i.test(query)) {
            field = 'claimAmount'
        } else if (/charge|charges|line item/i.test(query)) {
            field = 'lineItemCharge'
        } else if (/paid|payment|amount paid/i.test(query)) {
            field = 'amountPaid'
        } else if (/due|balance|owed|balance due/i.test(query)) {
            field = 'balanceDue'
        } else {
            field = 'claimAmount'
        }
    }
    
    return { field, type: aggregationType }
}

export async function handleAggregationQuery(queryText: string, filter?: string) {
    const aggregationInfo = detectAggregationQuery(queryText)
    
    if (!aggregationInfo) {
        return null
    }
    
    const { field, type } = aggregationInfo
    
    const request: AggregationRequest = {
        field,
        aggregation_type: type,
        filter,
        query: queryText
    }
    
    return await performAggregation(request)
}

export function formatAggregationResult(result: any, queryText: string): string {
    if (!result || result.error) {
        return `I couldn't perform that calculation. ${result?.error || 'Please try a different query.'}`
    }
    
    const { field, aggregation_type, result: value, count } = result
    
    const fieldName = field.replace(/([A-Z])/g, ' $1').toLowerCase()
    const formattedValue = typeof value === 'number' ? `$${value.toFixed(2)}` : value
    
    let response = ''
    
    switch (aggregation_type) {
        case 'avg':
            response = `The average ${fieldName} is ${formattedValue}, based on ${count} records.`
            break
        case 'sum':
            response = `The total ${fieldName} is ${formattedValue}, summed across ${count} records.`
            break
        case 'min':
            response = `The minimum ${fieldName} is ${formattedValue}, from ${count} records analyzed.`
            break
        case 'max':
            response = `The maximum ${fieldName} is ${formattedValue}, from ${count} records analyzed.`
            break
        case 'count':
            response = `There are ${count} records with ${fieldName} values.`
            break
        default:
            response = `The ${aggregation_type} of ${fieldName} is ${formattedValue}, based on ${count} records.`
    }
    
    return response
}